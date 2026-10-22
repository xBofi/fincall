"""Almacén privado de audio de llamadas.

- Ficheros WAV en un directorio privado del servidor (fuera de `static`), nunca servidos por URL
  pública persistente.
- Acceso solo por URL firmada de corta duración (HMAC con secreto del servidor), y además
  autenticado por rol en la ruta que la sirve.
- Retención: cada pista tiene `expires_at` (14 días por defecto); `purge_expired` borra el
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

from building_call.recording import ConversationRecorder
from building_call.repos.operational import OperationalRepository


class AudioStore:
    def __init__(
        self,
        directory: str | Path,
        secret: str,
        retention_days: int = 14,
        url_seconds: int = 300,
    ) -> None:
        if not secret:
            raise ValueError("El almacén de audio necesita un secreto para firmar URLs")
        self.directory = Path(directory)
        self.secret = secret.encode("utf-8")
        self.retention = timedelta(days=retention_days)
        self.url_seconds = url_seconds

    # ----------------------------------------------------------------- escritura

    async def save_pcm(
        self,
        repo: OperationalRepository,
        call_id: str,
        channel: str,
        pcm: bytes,
        sample_rate: int,
    ) -> dict[str, Any] | None:
        """Guarda PCM16 mono como WAV y registra la pista. Devuelve la pista o None si vacío."""
        if not pcm:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        track_id = f"aud-{uuid4().hex[:16]}"
        path = self.directory / f"{track_id}.wav"
        checksum = _write_wav(path, pcm, sample_rate)
        now = datetime.now(UTC)
        track = {
            "id": track_id,
            "call_id": call_id,
            "channel": channel,
            "object_key": str(path),
            "mime": "audio/wav",
            "checksum": checksum,
            "duration_ms": int(len(pcm) / 2 / sample_rate * 1000),
            "created_at": now,
            "expires_at": now + self.retention,
            "deleted_at": None,
        }
        await repo.save_track(track)
        return track

    async def save_conversation(
        self, repo: OperationalRepository, call_id: str, recorder: ConversationRecorder
    ) -> list[dict[str, Any]]:
        """Guarda las pistas alineadas de la llamada (`mix`, `client`, `assistant`)."""
        saved_tracks = []
        for channel, pcm in recorder.tracks().items():
            track = await self.save_pcm(repo, call_id, channel, pcm, recorder.rate)
            if track is not None:
                saved_tracks.append(track)
        return saved_tracks

    # ----------------------------------------------------------------- lectura firmada

    def _signature(self, track_id: str, expires: int) -> str:
        return hmac.new(self.secret, f"{track_id}:{expires}".encode(), hashlib.sha256).hexdigest()

    def signed_url(self, track_id: str, now: datetime | None = None) -> str:
        expires = int((now or datetime.now(UTC)).timestamp()) + self.url_seconds
        return (
            f"/api/operations/audio/{track_id}?exp={expires}&signature={self._signature(track_id, expires)}"
        )

    def valid_signature(
        self, track_id: str, expires: int, signature: str, now: datetime | None = None
    ) -> bool:
        if expires < int((now or datetime.now(UTC)).timestamp()):
            return False
        return hmac.compare_digest(self._signature(track_id, expires), signature)

    def read(self, track: dict[str, Any]) -> bytes | None:
        path = Path(track["object_key"])
        if track.get("deleted_at") or not path.exists():
            return None
        return path.read_bytes()

    # ----------------------------------------------------------------- retención

    async def purge_expired(
        self, repo: OperationalRepository, now: datetime | None = None
    ) -> int:
        now = now or datetime.now(UTC)
        deleted_count = 0
        for track in await repo.expired_tracks(now):
            path = Path(track["object_key"])
            try:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            except OSError:
                continue
            await repo.mark_track_deleted(track["id"], now)
            deleted_count += 1
        return deleted_count


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> str:
    """Escribe PCM16 mono como WAV y devuelve su sha256."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    data = buffer.getvalue()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()
