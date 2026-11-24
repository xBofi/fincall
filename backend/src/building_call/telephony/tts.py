"""Síntesis de voz en servidor: telefonía y, si hay una configurada, también la consola web.

Contrato: `sintetizar(texto)` devuelve PCM16 mono a `rate` Hz; `fragmentos(texto)` lo entrega
por trozos según llega (la consola empieza a reproducir sin esperar la frase). Implementaciones:
- `ElevenLabsSynthesizer`: ElevenLabs Flash v2.5 por API, PCM 16 kHz en streaming. Medido
  2026-10-11: primer audio ~170 ms, frase completa ~420 ms (Piper local: ~300-580 ms).
- `PiperSynthesizer`: Piper local en proceso (`piper-tts`), modelo cargado una vez (~150 ms por
  frase en CPU), voces españolas (`es_ES-davefx-medium`, `es_ES-sharvard-medium`), sin red. Preferido.
- `WorkersAISynthesizer`: Cloudflare Workers AI (MeloTTS, `lang=es`) → MP3 → PCM con ffmpeg.
  Alternativa cuando no hay Piper; tarda más (~1 s) y depende de la red.
Ambas cachean las frases repetidas (saludo, despedida, preguntas fijas del guion).
"""

from __future__ import annotations

import asyncio
import audioop
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

from building_call import i18n

logger = logging.getLogger(__name__)


class VoiceSynthesizer(Protocol):
    name: str
    rate: int

    def available(self) -> bool: ...

    async def synthesize(self, text: str, language: str | None = None) -> bytes: ...


def voice_for(persona: str | None, language: str) -> str | None:
    """The client's voice for this language (its persona), if it chose one."""
    from building_call.i18n import personas

    chosen = personas.for_call(persona, language)
    return chosen.voice if chosen and chosen.voice else None


async def fragments(
    tts: VoiceSynthesizer, text: str, language: str | None = None, voice: str | None = None
) -> AsyncIterator[bytes]:
    """Audio por trozos si el sintetizador sabe darlo así; si no, la frase entera de una vez.
    `language`: el de la llamada; None = el activo. `voice`: la de la persona del cliente."""
    if isinstance(tts, ElevenLabsSynthesizer):
        async for chunk in tts.fragments(text, language, voice):
            yield chunk
    elif isinstance(tts, FallbackSynthesizer):
        async for chunk in tts.fragments(text, language, voice):
            yield chunk
    else:
        yield await tts.synthesize(text, language)


_SPANISH_ORDINALS = (
    "", "primer", "segund", "tercer", "cuart", "quint", "sext", "séptim", "octav", "noven", "décim",
)  # fmt: skip
_ENGLISH_ORDINALS = (
    "", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
    "tenth",
)  # fmt: skip
_ORDINAL = re.compile(r"\b(10|[1-9])\s?([ºª])")


def text_for_voice(text: str, language: str | None = None) -> str:
    """Lo que la voz leería mal: "1º A" sonaba "un hilo A" con ElevenLabs (llamada 2026-10-11).
    Español: "primero A"; inglés: "first" (una plantilla editada o una etiqueta antigua pueden
    traer "º"); el resto de idiomas no usa "º"."""
    code = language or i18n.active_code()
    if code == "es":
        text = _ORDINAL.sub(
            lambda m: _SPANISH_ORDINALS[int(m[1])] + ("o" if m[2] == "º" else "a"), text
        )
    elif code == "en":
        text = _ORDINAL.sub(lambda m: _ENGLISH_ORDINALS[int(m[1])], text)
    return " ".join(text.split())


class _Cache:
    def __init__(self, capacity: int = 200) -> None:
        self.capacity = capacity
        self._data: OrderedDict[str, bytes] = OrderedDict()

    def get(self, key: str) -> bytes | None:
        pcm = self._data.get(key)
        if pcm is not None:
            self._data.move_to_end(key)
        return pcm

    def put(self, key: str, pcm: bytes) -> None:
        self._data[key] = pcm
        self._data.move_to_end(key)
        while len(self._data) > self.capacity:
            self._data.popitem(last=False)


class UnsupportedLanguage(RuntimeError):
    """The synthesizer can't speak the call's language: the next one in the chain should."""


class UnavailableSynthesizer:
    name = "none"
    rate = 16000

    def available(self) -> bool:
        return False

    async def synthesize(self, text: str, language: str | None = None) -> bytes:
        raise RuntimeError("No hay TTS de servidor configurado (BC_TTS / BC_PIPER_MODEL)")


class PiperSynthesizer:
    """Piper en el propio proceso (paquete `piper-tts`): el modelo se carga una vez y cada frase
    tarda decenas de milisegundos. Medido 2026-08-29: por subproceso costaba ~2,5 s por frase
    (carga del modelo en cada ejecución), inaceptable para una llamada; en proceso, ~150 ms.
    Si el paquete no está instalado se recurre al binario `piper` por subproceso."""

    def __init__(self, binary: str, model: str, timeout_s: float = 15.0) -> None:
        self.binary = shutil.which(binary) or binary
        self.model = model
        self.timeout_s = timeout_s
        self.name = f"piper/{Path(model).stem}" if model else "piper"
        self.rate = self._model_rate(model)
        self._cache = _Cache()
        self._voice: Any = None
        self._mutex = asyncio.Lock()

    @staticmethod
    def _model_rate(model: str) -> int:
        """Los modelos de Piper traen `<modelo>.onnx.json` con `audio.sample_rate`."""
        try:
            cfg = json.loads(Path(model + ".json").read_text(encoding="utf-8"))
            return int(cfg["audio"]["sample_rate"])
        except Exception:  # noqa: BLE001
            return 22050

    @staticmethod
    def _package_available() -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    def available(self) -> bool:
        if not self.model or not Path(self.model).exists():
            return False
        return self._package_available() or shutil.which(self.binary) is not None

    def _synthesize_in_process(self, text: str) -> bytes:
        from piper import PiperVoice

        if self._voice is None:
            self._voice = PiperVoice.load(self.model)
            self.rate = int(self._voice.config.sample_rate)
        return b"".join(chunk.audio_int16_bytes for chunk in self._voice.synthesize(text))

    async def _synthesize_subprocess(self, text: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self.binary,
            "--model",
            self.model,
            "--output_raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, err = await asyncio.wait_for(
                proc.communicate(text.encode("utf-8") + b"\n"), timeout=self.timeout_s
            )
        except TimeoutError:
            proc.kill()
            raise RuntimeError("Piper no respondió a tiempo") from None
        if proc.returncode != 0:
            raise RuntimeError(
                f"Piper falló ({proc.returncode}): {err.decode(errors='ignore')[:200]}"
            )
        return output

    async def warm_up(self) -> None:
        """Carga el modelo al arrancar para que la primera llamada no pague ~1 s."""
        if self._package_available() and self.available():
            async with self._mutex:
                await asyncio.to_thread(self._synthesize_in_process, "Hola.")

    async def synthesize(self, text: str, language: str | None = None) -> bytes:
        text = text_for_voice(text, language)
        if not text:
            return b""
        if (pcm := self._cache.get(text)) is not None:
            return pcm
        if self._package_available():
            # onnxruntime no es reentrante por sesión. Si vence el timeout el hilo sigue usando la
            # sesión: el cerrojo se suelta cuando el hilo termina, no cuando se rinde quien espera
            await self._mutex.acquire()
            hilo = asyncio.ensure_future(asyncio.to_thread(self._synthesize_in_process, text))
            hilo.add_done_callback(lambda _: self._mutex.release())
            output = await asyncio.wait_for(asyncio.shield(hilo), timeout=self.timeout_s)
        else:
            async with self._mutex:
                output = await self._synthesize_subprocess(text)
        self._cache.put(text, output)
        return output


class WorkersAISynthesizer:
    rate = 16000

    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = "@cf/myshell-ai/melotts",
        timeout_s: float = 20.0,
        ffmpeg: str = "ffmpeg",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.timeout_s = timeout_s
        self.ffmpeg = shutil.which(ffmpeg) or ffmpeg
        self.name = f"workers-ai/{model}"
        self._client = client
        self._cache = _Cache()

    def available(self) -> bool:
        return bool(self.account_id and self.api_token) and shutil.which(self.ffmpeg) is not None

    async def _request_mp3(self, text: str, lang: str) -> bytes:
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.model}"
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                json={"prompt": text, "lang": lang},
            )
        finally:
            if self._client is None:
                await client.aclose()
        r.raise_for_status()
        body: Any = r.json()
        audio_b64 = (body.get("result") or {}).get("audio") if isinstance(body, dict) else None
        if not audio_b64:
            raise RuntimeError(f"Workers AI no devolvió audio: {str(body)[:200]}")
        return base64.b64decode(audio_b64)

    async def _mp3_to_pcm(self, mp3: bytes) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-loglevel", "error", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(self.rate), "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        pcm, err = await asyncio.wait_for(proc.communicate(mp3), timeout=self.timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg falló: {err.decode(errors='ignore')[:200]}")
        return pcm

    async def synthesize(self, text: str, language: str | None = None) -> bytes:
        spec = i18n.get(language or i18n.active_code())
        if not spec.tts.melotts_lang:
            raise UnsupportedLanguage(f"MeloTTS no habla {spec.name} ({spec.code})")
        text = text_for_voice(text, spec.code)
        if not text:
            return b""
        key = f"{spec.code}:{text}"
        if (pcm := self._cache.get(key)) is not None:
            return pcm
        pcm = await self._mp3_to_pcm(await self._request_mp3(text, spec.tts.melotts_lang))
        self._cache.put(key, pcm)
        return pcm


class ElevenLabsSynthesizer:
    rate = 16000

    def __init__(
        self,
        api_key: str,
        voice: str,
        model: str = "eleven_v4_turbo",
        url: str = "https://api.elevenlabs.io",
        timeout_s: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.voice = voice
        self.model = model
        self.url = url.rstrip("/")
        self.name = f"elevenlabs/{model}"
        # Cliente persistente: reutiliza la conexión TLS entre frases (~100 ms menos por frase)
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._cache = _Cache()

    def available(self) -> bool:
        return bool(self.api_key and self.voice)

    async def warm_up(self) -> None:
        """Abre la conexión antes de la primera llamada; sin sintetizar (cada carácter se paga)."""
        try:
            await self._client.get(f"{self.url}/v1/models", headers={"xi-api-key": self.api_key})
        except httpx.HTTPError as e:
            logger.warning("ElevenLabs no responde al precalentar: %s", type(e).__name__)

    def voice_for(self, language: str) -> str:
        """The language's own voice if the locale file names one, else the configured voice."""
        return i18n.get(language).tts.elevenlabs_voice or self.voice

    def speaks(self, language: str) -> bool:
        return bool(i18n.get(language).tts.elevenlabs_language)

    async def fragments(
        self, text: str, language: str | None = None, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        spec = i18n.get(language or i18n.active_code())
        if not spec.tts.elevenlabs_language:
            raise UnsupportedLanguage(f"ElevenLabs no está configurado para {spec.name}")
        text = text_for_voice(text, spec.code)
        if not text:
            return
        voice = voice or self.voice_for(spec.code)
        key = f"{spec.code}:{voice}:{text}"
        if (pcm := self._cache.get(key)) is not None:
            yield pcm
            return
        url = f"{self.url}/v1/text-to-speech/{voice}/stream?output_format=pcm_{self.rate}"
        # language_code: ISO 639-1, accepted by the v3/v4 models for all our languages
        body = {"text": text, "model_id": self.model, "language_code": spec.tts.elevenlabs_language}
        full = bytearray()
        remainder = b""  # un byte suelto entre trozos: PCM16 va por pares
        async with self._client.stream(
            "POST", url, headers={"xi-api-key": self.api_key}, json=body
        ) as r:
            if r.status_code != 200:
                detail = (await r.aread())[:200].decode(errors="ignore")
                raise RuntimeError(f"ElevenLabs error {r.status_code}: {detail}")
            async for chunk in r.aiter_bytes():
                chunk = remainder + chunk
                cut = len(chunk) - len(chunk) % 2
                chunk, remainder = chunk[:cut], chunk[cut:]
                if chunk:
                    full += chunk
                    yield chunk
        self._cache.put(key, bytes(full))

    async def synthesize(
        self, text: str, language: str | None = None, voice: str | None = None
    ) -> bytes:
        return b"".join([chunk async for chunk in self.fragments(text, language, voice)])


class FallbackSynthesizer:
    """Per-language provider chain: for each call, the first synthesizer that speaks its
    language and answers. ElevenLabs (v3/v4) speaks all six languages; Piper has local voices
    for es, en, ca, ar and hi (none for Urdu); MeloTTS only en and es. Every member's audio is
    resampled to `rate`, so the phone bridge and the console see one sample rate."""

    def __init__(self, members: list[tuple[VoiceSynthesizer, frozenset[str]]], rate: int = 16000):
        self.members = members
        self.rate = rate
        self.name = "+".join(m.name for m, _ in members) or "none"

    def available(self) -> bool:
        return any(m.available() for m, _ in self.members)

    def speaks(self, language: str) -> bool:
        return any(m.available() and language in langs for m, langs in self.members)

    def chain(self, language: str) -> list[VoiceSynthesizer]:
        return [m for m, langs in self.members if language in langs and m.available()]

    async def warm_up(self) -> None:
        for member, _ in self.members:
            if hasattr(member, "warm_up"):
                await member.warm_up()

    async def fragments(
        self, text: str, language: str | None = None, voice: str | None = None
    ) -> AsyncIterator[bytes]:
        code = i18n.get(language or i18n.active_code()).code
        chain = self.chain(code)
        if not chain:
            raise UnsupportedLanguage(f"Ningún TTS configurado habla {code}")
        for i, member in enumerate(chain):
            converter: Any = None
            sent = False
            try:
                async for chunk in fragments(member, text, code, voice):
                    if member.rate != self.rate:
                        chunk, converter = audioop.ratecv(
                            chunk, 2, 1, member.rate, self.rate, converter
                        )
                    sent = True
                    yield chunk
                return
            except Exception:
                # Once audio went out, switching voice mid-sentence is worse than failing
                if sent or i == len(chain) - 1:
                    raise
                logger.warning("TTS %s falló en %s; se prueba el siguiente", member.name, code)

    async def synthesize(self, text: str, language: str | None = None) -> bytes:
        return b"".join([chunk async for chunk in self.fragments(text, language)])


def _piper_voices(settings: Any) -> list[tuple[VoiceSynthesizer, frozenset[str]]]:
    """One Piper model per language: `<BC_PIPER_DIR>/<piper_voice>.onnx` from the locale file;
    `BC_PIPER_MODEL` keeps serving the default language."""
    voices: list[tuple[VoiceSynthesizer, frozenset[str]]] = []
    default = i18n.default_code()
    if settings.piper_model:
        voices.append(
            (PiperSynthesizer(settings.piper_bin, settings.piper_model), frozenset({default}))
        )
    if settings.piper_dir:
        for code in i18n.enabled():
            stem = i18n.get(code).tts.piper_voice
            path = Path(settings.piper_dir) / f"{stem}.onnx"
            if stem and path.exists() and not (code == default and settings.piper_model):
                voices.append((PiperSynthesizer(settings.piper_bin, str(path)), frozenset({code})))
    return voices


def create_tts(settings: Any) -> VoiceSynthesizer:
    """`BC_TTS`: auto | elevenlabs | piper | workers_ai | none. The chosen provider goes first;
    for a language it can't speak, the next available one that can (see `FallbackSynthesizer`).
    If the chosen one covers every enabled language on its own, it is returned as is."""
    mode = settings.tts
    if mode in ("none", "ninguno"):
        return UnavailableSynthesizer()
    enabled = frozenset(i18n.enabled())
    eleven = ElevenLabsSynthesizer(
        settings.elevenlabs_api_key,
        settings.elevenlabs_voice,
        settings.elevenlabs_model,
        settings.elevenlabs_url,
    )
    workers = WorkersAISynthesizer(
        settings.cloudflare_account_id,
        settings.cloudflare_api_token,
        settings.cloudflare_tts_model,
    )
    providers: dict[str, list[tuple[VoiceSynthesizer, frozenset[str]]]] = {
        "elevenlabs": [(eleven, frozenset(c for c in enabled if eleven.speaks(c)))],
        "piper": _piper_voices(settings),
        "workers_ai": [(workers, frozenset(c for c in enabled if i18n.get(c).tts.melotts_lang))],
    }
    order = ["piper", "workers_ai", "elevenlabs"] if mode == "auto" else [mode]
    order += [p for p in ("elevenlabs", "piper", "workers_ai") if p not in order]
    first = providers.get(order[0], [])
    if len(first) == 1 and first[0][1] >= enabled and mode != "auto":
        return first[0][0]
    members = [m for name in order for m in providers[name] if m[0].available() or name == mode]
    if not members:
        return UnavailableSynthesizer()
    return FallbackSynthesizer(members)
