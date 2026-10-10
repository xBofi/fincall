"""Almacén privado de audio de llamadas.

- Ficheros WAV en un directorio privado del servidor (fuera de `static`), nunca servidos por URL
  pública persistente.
- Acceso solo por URL firmada de corta duración (HMAC con secreto del servidor), y además
  autenticado por rol en la ruta que la sirve.
- Retención: cada pista tiene `expira_en` (14 días por defecto); `purgar_vencidas` borra el
  fichero y marca la fila como eliminada.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from building_call.grabacion import GrabadorConversacion
from building_call.repos.operativo import RepositorioOperativo


class AlmacenAudio:
    def __init__(
        self,
        directorio: str | Path,
        secreto: str,
        retencion_dias: int = 14,
        url_segundos: int = 300,
    ) -> None:
        if not secreto:
            raise ValueError("El almacén de audio necesita un secreto para firmar URLs")
        self.directorio = Path(directorio)
        self.secreto = secreto.encode("utf-8")
        self.retencion = timedelta(days=retencion_dias)
        self.url_segundos = url_segundos

    # ----------------------------------------------------------------- escritura

    async def guardar_pcm(
        self,
        repo: RepositorioOperativo,
        llamada_id: str,
        canal: str,
        pcm: bytes,
        sample_rate: int,
    ) -> dict[str, Any] | None:
        """Guarda PCM16 mono como WAV y registra la pista. Devuelve la pista o None si vacío."""
        if not pcm:
            return None
        self.directorio.mkdir(parents=True, exist_ok=True)
        pista_id = f"aud-{uuid4().hex[:16]}"
        ruta = self.directorio / f"{pista_id}.wav"
        checksum = _escribir_wav(ruta, pcm, sample_rate)
        ahora = datetime.now(UTC)
        pista = {
            "id": pista_id,
            "llamada_id": llamada_id,
            "canal": canal,
            "clave_objeto": str(ruta),
            "mime": "audio/wav",
            "checksum": checksum,
            "duracion_ms": int(len(pcm) / 2 / sample_rate * 1000),
            "creada_en": ahora,
            "expira_en": ahora + self.retencion,
            "eliminada_en": None,
        }
        await repo.guardar_pista(pista)
        return pista

    async def guardar_conversacion(
        self, repo: RepositorioOperativo, llamada_id: str, grabador: GrabadorConversacion
    ) -> list[dict[str, Any]]:
        """Guarda las pistas alineadas de la llamada (`mezcla`, `cliente`, `asistente`)."""
        guardadas = []
        for canal, pcm in grabador.pistas().items():
            pista = await self.guardar_pcm(repo, llamada_id, canal, pcm, grabador.tasa)
            if pista is not None:
                guardadas.append(pista)
        return guardadas

    # ----------------------------------------------------------------- lectura firmada

    def _firma(self, pista_id: str, expira: int) -> str:
        return hmac.new(self.secreto, f"{pista_id}:{expira}".encode(), hashlib.sha256).hexdigest()

    def url_firmada(self, pista_id: str, ahora: datetime | None = None) -> str:
        expira = int((ahora or datetime.now(UTC)).timestamp()) + self.url_segundos
        return (
            f"/api/operaciones/audio/{pista_id}?exp={expira}&firma={self._firma(pista_id, expira)}"
        )

    def firma_valida(
        self, pista_id: str, expira: int, firma: str, ahora: datetime | None = None
    ) -> bool:
        if expira < int((ahora or datetime.now(UTC)).timestamp()):
            return False
        return hmac.compare_digest(self._firma(pista_id, expira), firma)

    def leer(self, pista: dict[str, Any]) -> bytes | None:
        ruta = Path(pista["clave_objeto"])
        if pista.get("eliminada_en") or not ruta.exists():
            return None
        return ruta.read_bytes()

    # ----------------------------------------------------------------- retención

    async def purgar_vencidas(
        self, repo: RepositorioOperativo, ahora: datetime | None = None
    ) -> int:
        ahora = ahora or datetime.now(UTC)
        borradas = 0
        for pista in await repo.pistas_vencidas(ahora):
            ruta = Path(pista["clave_objeto"])
            try:
                await asyncio.to_thread(ruta.unlink, missing_ok=True)
            except OSError:
                continue
            await repo.marcar_pista_eliminada(pista["id"], ahora)
            borradas += 1
        return borradas


def _escribir_wav(ruta: Path, pcm: bytes, sample_rate: int) -> str:
    """Escribe PCM16 mono como WAV y devuelve su sha256."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    datos = buffer.getvalue()
    ruta.write_bytes(datos)
    return hashlib.sha256(datos).hexdigest()
