# Backend: FastAPI + LangGraph con uv, sobre Ubuntu (imagen slim). Sin secretos en la imagen.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# libgomp: lo necesita onnxruntime (Piper). La voz española se descarga en la imagen (sin secretos).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --no-install-project --extra tts
ADD https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx /app/.voces/
ADD https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json /app/.voces/

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --frozen --no-dev --extra tts

ENV PATH="/app/.venv/bin:$PATH" BC_PIPER_MODEL=/app/.voces/es_ES-davefx-medium.onnx
EXPOSE 8000
CMD ["uvicorn", "building_call.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
