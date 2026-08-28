"""Configuración de infraestructura y secretos.

Solo variables de entorno (`.env` en local, variables/secretos de CI en despliegue).
La configuración de negocio (fincas, especialistas, reglas) NO va aquí: vive en PostgreSQL.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BC_", env_file=".env", extra="ignore")

    # "memoria" permite validar los agentes sin base de datos; "postgres" es el modo real.
    repositorio: str = "memoria"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/building_call"

    # LLM: "ollama" (local) | "workers_ai" (Cloudflare) | "carrera" (ambos, gana el primero)
    llm: str = "ollama"

    # Ollama local. qwen3.5:4b: 96% exactitud / ~600 ms por interpretación (evals/comparar_modelos.py)
    ollama_url: str = "http://localhost:11434"
    ollama_modelo: str = "qwen3.5:4b"
    ollama_timeout_s: float = 30.0
    ollama_num_predict: int = 160
    ollama_num_ctx: int = 2048

    # Cloudflare Workers AI. qwen3.8-27b sin razonamiento: 100% exactitud / p50 ~590 ms, p95 ~1200 ms
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_modelo: str = "@cf/qwen/qwen3.8-27b"
    cloudflare_timeout_s: float = 20.0

    # STT por lotes en servidor: "ninguno" (el navegador transcribe) | "whisper" (faster-whisper local)
    stt: str = "ninguno"
    whisper_modelo: str = "small"
    whisper_dispositivo: str = "auto"  # auto | cuda | cpu
    whisper_computo: str = "auto"  # auto | int8 | float16 | int8_float16

    # STT en streaming: AssemblyAI, SOLO endpoint europeo (se valida al arrancar)
    assemblyai_api_key: str = ""
    assemblyai_api_base: str = "https://api.eu.assemblyai.com"
    assemblyai_streaming_host: str = "streaming.eu.assemblyai.com"

    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
