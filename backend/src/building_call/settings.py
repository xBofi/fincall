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

    # LLM: "auto" (carrera si hay credenciales de Cloudflare, si no ollama) | "ollama" | "workers_ai" |
    # "carrera" (Cloudflare preferido por exactitud; si el local acaba antes, espera este margen).
    # Medido 2026-08-28: local 92%/p95 828 ms; Cloudflare 100%/p95 10 s; carrera+150 ms 96%/p95 831 ms.
    llm: str = "auto"
    carrera_espera_extra_ms: int = 150
    # Redacción de respuestas: "llm" (paráfrasis controlada, +1 llamada por turno) | "plantilla"
    redaccion: str = "llm"

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
    # Fin de turno (ver SesionStreamingAssemblyAI). Medido 2026-08-28: con confianza 0.8 y máximo
    # 2400 ms, las frases cortas ("Calle Mayor 12") esperaban el máximo: 3,7 s de latencia.
    assemblyai_eot_confianza: float = 0.7
    assemblyai_eot_silencio_ms: int = 220
    assemblyai_max_silencio_ms: int = 800

    cors_origins: list[str] = ["http://localhost:5173"]
    frontend_url: str = "http://localhost:5173"

    # Acceso a la consola operativa (handoff Codex). "google": OIDC real; "desarrollo": owner
    # local sin Google, SOLO para desarrollo. Sin secreto o sin allowlist el acceso queda
    # deshabilitado.
    auth_modo: str = "google"
    auth_dev_email: str = "desarrollo@localhost"
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_url: str = "http://localhost:8000/api/auth/callback"
    acceso_owners: str = ""  # correos separados por comas
    acceso_administradores: str = ""
    acceso_calidad: str = ""
    sesion_secreto: str = ""  # firma cookies de estado OAuth y URLs de audio
    sesion_cookie: str = "bc_sesion"
    sesion_horas: int = 12

    # Audio de llamadas: directorio privado, URL firmada de corta duración, retención
    audio_dir: str = ".audio"
    audio_retencion_dias: int = 14
    audio_url_segundos: int = 300


settings = Settings()
