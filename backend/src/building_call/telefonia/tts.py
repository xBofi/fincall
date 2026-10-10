"""Síntesis de voz en servidor: telefonía y, si hay una configurada, también la consola web.

Contrato: `sintetizar(texto)` devuelve PCM16 mono a `tasa` Hz; `fragmentos(texto)` lo entrega
por trozos según llega (la consola empieza a reproducir sin esperar la frase). Implementaciones:
- `SintetizadorElevenLabs`: ElevenLabs Flash v2.5 por API, PCM 16 kHz en streaming. Medido
  2026-10-11: primer audio ~170 ms, frase completa ~420 ms (Piper local: ~300-580 ms).
- `SintetizadorPiper`: Piper local en proceso (`piper-tts`), modelo cargado una vez (~150 ms por
  frase en CPU), voces españolas (`es_ES-davefx-medium`, `es_ES-sharvard-medium`), sin red. Preferido.
- `SintetizadorWorkersAI`: Cloudflare Workers AI (MeloTTS, `lang=es`) → MP3 → PCM con ffmpeg.
  Alternativa cuando no hay Piper; tarda más (~1 s) y depende de la red.
Ambas cachean las frases repetidas (saludo, despedida, preguntas fijas del guion).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class SintetizadorVoz(Protocol):
    nombre: str
    tasa: int

    def disponible(self) -> bool: ...

    async def sintetizar(self, texto: str) -> bytes: ...


async def fragmentos(tts: SintetizadorVoz, texto: str) -> AsyncIterator[bytes]:
    """Audio por trozos si el sintetizador sabe darlo así; si no, la frase entera de una vez."""
    if isinstance(tts, SintetizadorElevenLabs):
        async for trozo in tts.fragmentos(texto):
            yield trozo
    else:
        yield await tts.sintetizar(texto)


_ORDINALES = (
    "", "primer", "segund", "tercer", "cuart", "quint", "sext", "séptim", "octav", "noven", "décim",
)  # fmt: skip
_ORDINAL = re.compile(r"\b(10|[1-9])\s?([ºª])")


def texto_para_voz(texto: str) -> str:
    """Lo que la voz leería mal: "1º A" sonaba "un hilo A" con ElevenLabs (llamada 2026-10-11)."""
    texto = _ORDINAL.sub(lambda m: _ORDINALES[int(m[1])] + ("o" if m[2] == "º" else "a"), texto)
    return " ".join(texto.split())


class _Cache:
    def __init__(self, capacidad: int = 200) -> None:
        self.capacidad = capacidad
        self._datos: OrderedDict[str, bytes] = OrderedDict()

    def get(self, clave: str) -> bytes | None:
        pcm = self._datos.get(clave)
        if pcm is not None:
            self._datos.move_to_end(clave)
        return pcm

    def put(self, clave: str, pcm: bytes) -> None:
        self._datos[clave] = pcm
        self._datos.move_to_end(clave)
        while len(self._datos) > self.capacidad:
            self._datos.popitem(last=False)


class SintetizadorNoDisponible:
    nombre = "ninguno"
    tasa = 16000

    def disponible(self) -> bool:
        return False

    async def sintetizar(self, texto: str) -> bytes:
        raise RuntimeError("No hay TTS de servidor configurado (BC_TTS / BC_PIPER_MODELO)")


class SintetizadorPiper:
    """Piper en el propio proceso (paquete `piper-tts`): el modelo se carga una vez y cada frase
    tarda decenas de milisegundos. Medido 2026-08-29: por subproceso costaba ~2,5 s por frase
    (carga del modelo en cada ejecución), inaceptable para una llamada; en proceso, ~150 ms.
    Si el paquete no está instalado se recurre al binario `piper` por subproceso."""

    def __init__(self, binario: str, modelo: str, timeout_s: float = 15.0) -> None:
        self.binario = shutil.which(binario) or binario
        self.modelo = modelo
        self.timeout_s = timeout_s
        self.nombre = f"piper/{Path(modelo).stem}" if modelo else "piper"
        self.tasa = self._tasa_del_modelo(modelo)
        self._cache = _Cache()
        self._voz: Any = None
        self._cerrojo = asyncio.Lock()

    @staticmethod
    def _tasa_del_modelo(modelo: str) -> int:
        """Los modelos de Piper traen `<modelo>.onnx.json` con `audio.sample_rate`."""
        try:
            cfg = json.loads(Path(modelo + ".json").read_text(encoding="utf-8"))
            return int(cfg["audio"]["sample_rate"])
        except Exception:  # noqa: BLE001
            return 22050

    @staticmethod
    def _paquete_disponible() -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    def disponible(self) -> bool:
        if not self.modelo or not Path(self.modelo).exists():
            return False
        return self._paquete_disponible() or shutil.which(self.binario) is not None

    def _sintetizar_en_proceso(self, texto: str) -> bytes:
        from piper import PiperVoice

        if self._voz is None:
            self._voz = PiperVoice.load(self.modelo)
            self.tasa = int(self._voz.config.sample_rate)
        return b"".join(trozo.audio_int16_bytes for trozo in self._voz.synthesize(texto))

    async def _sintetizar_subproceso(self, texto: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self.binario,
            "--model",
            self.modelo,
            "--output_raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            salida, err = await asyncio.wait_for(
                proc.communicate(texto.encode("utf-8") + b"\n"), timeout=self.timeout_s
            )
        except TimeoutError:
            proc.kill()
            raise RuntimeError("Piper no respondió a tiempo") from None
        if proc.returncode != 0:
            raise RuntimeError(
                f"Piper falló ({proc.returncode}): {err.decode(errors='ignore')[:200]}"
            )
        return salida

    async def precalentar(self) -> None:
        """Carga el modelo al arrancar para que la primera llamada no pague ~1 s."""
        if self._paquete_disponible() and self.disponible():
            async with self._cerrojo:
                await asyncio.to_thread(self._sintetizar_en_proceso, "Hola.")

    async def sintetizar(self, texto: str) -> bytes:
        texto = texto_para_voz(texto)
        if not texto:
            return b""
        if (pcm := self._cache.get(texto)) is not None:
            return pcm
        if self._paquete_disponible():
            # onnxruntime no es reentrante por sesión. Si vence el timeout el hilo sigue usando la
            # sesión: el cerrojo se suelta cuando el hilo termina, no cuando se rinde quien espera
            await self._cerrojo.acquire()
            hilo = asyncio.ensure_future(asyncio.to_thread(self._sintetizar_en_proceso, texto))
            hilo.add_done_callback(lambda _: self._cerrojo.release())
            salida = await asyncio.wait_for(asyncio.shield(hilo), timeout=self.timeout_s)
        else:
            async with self._cerrojo:
                salida = await self._sintetizar_subproceso(texto)
        self._cache.put(texto, salida)
        return salida


class SintetizadorWorkersAI:
    tasa = 16000

    def __init__(
        self,
        account_id: str,
        api_token: str,
        modelo: str = "@cf/myshell-ai/melotts",
        timeout_s: float = 20.0,
        ffmpeg: str = "ffmpeg",
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.modelo = modelo
        self.timeout_s = timeout_s
        self.ffmpeg = shutil.which(ffmpeg) or ffmpeg
        self.nombre = f"workers-ai/{modelo}"
        self._cliente = cliente
        self._cache = _Cache()

    def disponible(self) -> bool:
        return bool(self.account_id and self.api_token) and shutil.which(self.ffmpeg) is not None

    async def _pedir_mp3(self, texto: str) -> bytes:
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.modelo}"
        )
        cliente = self._cliente or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            r = await cliente.post(
                url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                json={"prompt": texto, "lang": "es"},
            )
        finally:
            if self._cliente is None:
                await cliente.aclose()
        r.raise_for_status()
        cuerpo: Any = r.json()
        audio_b64 = (cuerpo.get("result") or {}).get("audio") if isinstance(cuerpo, dict) else None
        if not audio_b64:
            raise RuntimeError(f"Workers AI no devolvió audio: {str(cuerpo)[:200]}")
        return base64.b64decode(audio_b64)

    async def _mp3_a_pcm(self, mp3: bytes) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-loglevel", "error", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(self.tasa), "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        pcm, err = await asyncio.wait_for(proc.communicate(mp3), timeout=self.timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg falló: {err.decode(errors='ignore')[:200]}")
        return pcm

    async def sintetizar(self, texto: str) -> bytes:
        texto = texto_para_voz(texto)
        if not texto:
            return b""
        if (pcm := self._cache.get(texto)) is not None:
            return pcm
        pcm = await self._mp3_a_pcm(await self._pedir_mp3(texto))
        self._cache.put(texto, pcm)
        return pcm


class SintetizadorElevenLabs:
    tasa = 16000

    def __init__(
        self,
        api_key: str,
        voz: str,
        modelo: str = "eleven_v4",
        url: str = "https://api.elevenlabs.io",
        timeout_s: float = 10.0,
        cliente: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.voz = voz
        self.modelo = modelo
        self.url = url.rstrip("/")
        self.nombre = f"elevenlabs/{modelo}"
        # Cliente persistente: reutiliza la conexión TLS entre frases (~100 ms menos por frase)
        self._cliente = cliente or httpx.AsyncClient(timeout=timeout_s)
        self._cache = _Cache()

    def disponible(self) -> bool:
        return bool(self.api_key and self.voz)

    async def precalentar(self) -> None:
        """Abre la conexión antes de la primera llamada; sin sintetizar (cada carácter se paga)."""
        try:
            await self._cliente.get(f"{self.url}/v1/models", headers={"xi-api-key": self.api_key})
        except httpx.HTTPError as e:
            logger.warning("ElevenLabs no responde al precalentar: %s", type(e).__name__)

    async def fragmentos(self, texto: str) -> AsyncIterator[bytes]:
        texto = texto_para_voz(texto)
        if not texto:
            return
        if (pcm := self._cache.get(texto)) is not None:
            yield pcm
            return
        url = f"{self.url}/v1/text-to-speech/{self.voz}/stream?output_format=pcm_{self.tasa}"
        cuerpo = {"text": texto, "model_id": self.modelo, "language_code": "es"}
        completo = bytearray()
        resto = b""  # un byte suelto entre trozos: PCM16 va por pares
        async with self._cliente.stream(
            "POST", url, headers={"xi-api-key": self.api_key}, json=cuerpo
        ) as r:
            if r.status_code != 200:
                detalle = (await r.aread())[:200].decode(errors="ignore")
                raise RuntimeError(f"ElevenLabs error {r.status_code}: {detalle}")
            async for trozo in r.aiter_bytes():
                trozo = resto + trozo
                corte = len(trozo) - len(trozo) % 2
                trozo, resto = trozo[:corte], trozo[corte:]
                if trozo:
                    completo += trozo
                    yield trozo
        self._cache.put(texto, bytes(completo))

    async def sintetizar(self, texto: str) -> bytes:
        return b"".join([trozo async for trozo in self.fragmentos(texto)])


def crear_tts(settings: Any) -> SintetizadorVoz:
    """`BC_TTS`: auto (Piper si está, si no Workers AI) | elevenlabs | piper | workers_ai | ninguno."""
    modo = settings.tts
    if modo == "elevenlabs":
        return SintetizadorElevenLabs(
            settings.elevenlabs_api_key,
            settings.elevenlabs_voz,
            settings.elevenlabs_modelo,
            settings.elevenlabs_url,
        )
    piper = SintetizadorPiper(settings.piper_bin, settings.piper_modelo)
    workers = SintetizadorWorkersAI(
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_tts_modelo,
    )
    if modo == "piper":
        return piper
    if modo == "workers_ai":
        return workers
    if modo == "auto":
        if piper.disponible():
            return piper
        if workers.disponible():
            return workers
    return SintetizadorNoDisponible()
