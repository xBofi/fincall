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
    # "vertex" (Gemini en Vertex AI, solo Europa) |
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

    # Google Vertex AI (Gemini). Ubicación SOLO europea: "eu" (multi-región UE) o "europe-*"
    # (se valida al crear el cliente). Credenciales: JSON de cuenta de servicio en
    # `vertex_credenciales`; vacío = credenciales por defecto (`gcloud auth application-default login`).
    vertex_proyecto: str = ""
    vertex_ubicacion: str = "eu"
    vertex_modelo: str = "gemini-3.5-flash-lite"  # medido 2026-10-11: 98% exactitud, p50 626 / p95 722 ms (UE)
    vertex_credenciales: str = ""
    vertex_timeout_s: float = 20.0

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

    # Acceso a la consola operativa: usuarios internos con contraseña (sin proveedor externo).
    # Si no existe ningún usuario al arrancar se crea este owner con cambio de contraseña
    # obligatorio en el primer acceso.
    admin_inicial_usuario: str = "admin"
    admin_inicial_contrasena: str = "admin"
    login_max_intentos: int = 5  # fallos por usuario o por IP antes del bloqueo temporal
    login_bloqueo_s: int = 900
    sesion_secreto: str = ""  # firma las URLs de audio
    sesion_cookie: str = "bc_sesion"
    sesion_horas: int = 12

    # Telefonía real (proveedor Pendiente en la bóveda; Twilio es la primera implementación).
    # Cuando haya número: BC_TELEFONIA=twilio, BC_URL_PUBLICA=https://... (túnel o dominio),
    # credenciales de Twilio y `python -m building_call.telefonia.configurar`.
    telefonia: str = "ninguna"  # ninguna | twilio
    url_publica: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_numero: str = ""  # E.164, p. ej. +34912345678
    telefonia_palabras_bargein: int = 2
    # TTS de servidor (solo telefonía; la consola web habla con el navegador)
    tts: str = "auto"  # auto | elevenlabs | piper | workers_ai | ninguno
    # ElevenLabs (BC_TTS=elevenlabs). Sin residencia UE salvo plan Enterprise (BC_ELEVENLABS_URL).
    elevenlabs_api_key: str = ""
    elevenlabs_voz: str = ""  # voice ID, no el nombre
    # Medido 2026-10-11: v4 Turbo primer audio ~158 ms (como Flash v2.5) con mejor voz
    elevenlabs_modelo: str = "eleven_v4_turbo"
    elevenlabs_url: str = "https://api.elevenlabs.io"
    piper_bin: str = "piper"
    piper_modelo: str = ""  # ruta al .onnx (junto a su .onnx.json), p. ej. es_ES-davefx-medium
    cloudflare_tts_modelo: str = "@cf/myshell-ai/melotts"

    # Audio de llamadas: directorio privado, URL firmada de corta duración, retención
    audio_dir: str = ".audio"
    audio_retencion_dias: int = 14
    audio_url_segundos: int = 300


settings = Settings()
