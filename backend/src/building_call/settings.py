"""Configuración de infraestructura y secretos.

Solo variables de entorno (`.env` en local, variables/secretos de CI en despliegue).
La configuración de negocio (fincas, especialistas, reglas) NO va aquí: vive en PostgreSQL.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BC_", env_file=".env", extra="ignore")

    # "memoria" permite validar los agentes sin base de datos; "postgres" es el modo real.
    repository: str = "memory"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/building_call"

    # LLM: "auto" (carrera si hay credenciales de Cloudflare, si no ollama) | "ollama" | "workers_ai" |
    # "vertex" (Gemini en Vertex AI, solo Europa) |
    # "carrera" (Cloudflare preferido por exactitud; si el local acaba antes, espera este margen).
    # Medido 2026-08-28: local 92%/p95 828 ms; Cloudflare 100%/p95 10 s; carrera+150 ms 96%/p95 831 ms.
    llm: str = "auto"
    race_extra_wait_ms: int = 150
    # Redacción de respuestas: "llm" (paráfrasis controlada, +1 llamada por turno) | "plantilla"
    phrasing: str = "llm"

    # Ollama local. qwen3.5:4b: 96% exactitud / ~600 ms por interpretación (evals/compare_models.py)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_timeout_s: float = 30.0
    ollama_num_predict: int = 160
    ollama_num_ctx: int = 2048

    # Cloudflare Workers AI. qwen3.8-27b sin razonamiento: 100% exactitud / p50 ~590 ms, p95 ~1200 ms
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_model: str = "@cf/qwen/qwen3.8-27b"
    cloudflare_timeout_s: float = 20.0

    # Google Vertex AI (Gemini). Ubicación SOLO europea: "eu" (multi-región UE) o "europe-*"
    # (se valida al crear el cliente). Credenciales: JSON de cuenta de servicio en
    # `vertex_credentials`; vacío = credenciales por defecto (`gcloud auth application-default login`).
    vertex_project: str = ""
    vertex_location: str = "eu"
    vertex_model: str = (
        "gemini-3.5-flash-lite"  # medido 2026-10-11: 98% exactitud, p50 626 / p95 722 ms (UE)
    )
    vertex_credentials: str = ""
    vertex_timeout_s: float = 20.0

    # STT por lotes en servidor: "ninguno" (el navegador transcribe) | "whisper" (faster-whisper local)
    stt: str = "ninguno"
    whisper_model: str = "small"
    whisper_device: str = "auto"  # auto | cuda | cpu
    whisper_compute: str = "auto"  # auto | int8 | float16 | int8_float16

    # STT en streaming: AssemblyAI, SOLO endpoint europeo (se valida al arrancar)
    assemblyai_api_key: str = ""
    assemblyai_api_base: str = "https://api.eu.assemblyai.com"
    assemblyai_streaming_host: str = "streaming.eu.assemblyai.com"
    # Fin de turno (ver SesionStreamingAssemblyAI). Medido 2026-08-28: con confianza 0.8 y máximo
    # 2400 ms, las frases cortas ("Calle Mayor 12") esperaban el máximo: 3,7 s de latencia.
    assemblyai_eot_confidence: float = 0.7
    assemblyai_eot_silence_ms: int = 220
    assemblyai_max_silence_ms: int = 800
    # Modelo de AssemblyAI del agente de LiveKit
    assemblyai_model: str = "universal-3-6-pro"

    cors_origins: list[str] = ["http://localhost:5173"]

    # Acceso a la consola: solo con Google o Microsoft (decisión de Andreu, 2026-10-21). Quien
    # entra con BC_OWNER_EMAIL (correo verificado por el proveedor) es owner; el resto solicita
    # acceso y el owner lo concede. Claves solo en backend/.env.
    console_url: str = "http://localhost:5173"  # retorno: {url_consola}/api/auth/oidc/{p}/retorno
    owner_email: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    session_secret: str = ""  # firma las URLs de audio y la ida y vuelta al proveedor
    session_cookie: str = "bc_session"
    # La sesión dura 30 días y se alarga sola al usarla: no hay que volver a entrar cada día
    session_days: int = 30

    # Telefonía real (proveedor Pendiente en la bóveda; Twilio es la primera implementación).
    # Cuando haya número: BC_TELEPHONY=twilio, BC_PUBLIC_URL=https://... (túnel o dominio),
    # credenciales de Twilio y `python -m building_call.telephony.configure`.
    telephony: str = "ninguna"  # ninguna | twilio
    public_url: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_number: str = ""  # E.164, p. ej. +34912345678
    telephony_bargein_words: int = 2
    # TTS de servidor (solo telefonía; la consola web habla con el navegador)
    tts: str = "auto"  # auto | elevenlabs | piper | workers_ai | ninguno
    # ElevenLabs (BC_TTS=elevenlabs). Sin residencia UE salvo plan Enterprise (BC_ELEVENLABS_URL).
    elevenlabs_api_key: str = ""
    elevenlabs_voice: str = ""  # voice ID, no el nombre
    # Medido 2026-10-11: v4 Turbo primer audio ~158 ms (como Flash v2.5) con mejor voz
    elevenlabs_model: str = "eleven_v4_turbo"
    elevenlabs_url: str = "https://api.elevenlabs.io"
    piper_bin: str = "piper"
    piper_model: str = ""  # ruta al .onnx (junto a su .onnx.json), p. ej. es_ES-davefx-medium
    cloudflare_tts_model: str = "@cf/myshell-ai/melotts"

    # Audio de llamadas: directorio privado, URL firmada de corta duración, retención
    audio_dir: str = ".audio"
    audio_retention_days: int = 14
    audio_url_seconds: int = 300

    # LiveKit autoalojado (spec 2026-10-21): servidor de medios y agente de voz en otro proceso.
    # En desarrollo, `livekit-server --dev` usa devkey / secret.
    livekit_url: str = ""  # ws://localhost:7880
    # La que recibe la consola, si no es la misma: "/" = el origen de la consola (túnel ngrok,
    # con Vite reenviando /rtc a LiveKit)
    livekit_console_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    # Nombre con el que se registra el agente y que piden las salas en su despacho. Otro nombre
    # permite tener un segundo agente (pruebas, banco) en el mismo LiveKit sin quitarle salas
    livekit_agent: str = "fincall"
    # Turnos del agente de LiveKit (ver `building_call.voice.agent`). Silencio de fin de turno de
    # Universal-3 Pro: con frase acabada cierra tras el mínimo; si no, espera hasta el máximo.
    # Medido 2026-10-22 (frases del banco de voz, AssemblyAI EU): con 220 ms partía en dos 6 de
    # 32 frases en la pausa de una coma ("Sí. / Es correcto."); con 300 ms, 1 de 32, a cambio de
    # ~90 ms más de mediana. El camino WebSocket sigue con assemblyai_eot_silencio_ms.
    livekit_stt_silence_min_ms: int = 300
    livekit_stt_silence_max_ms: int = 800
    # Pasar a AssemblyAI lo último que dijo el bot (agent_context de Universal-3 Pro). Medido
    # 2026-10-22: con contexto cierra antes el turno tras "Sí." o "No." (3 de 32 frases partidas
    # frente a 1 de 32), así que va apagado
    livekit_stt_context: bool = False
    # Silencio con el que el VAD (silero) da por acabada la voz. Bastante menor que el mínimo de
    # AssemblyAI para saber, al cerrar AssemblyAI un turno, si el vecino ya ha vuelto a hablar. Con
    # 0,25 s el VAD seguía "oyendo" el final de la frase al cerrarse el turno y se retenían turnos
    # completos 1,2 s (medido 2026-10-22)
    livekit_vad_silence_s: float = 0.15
    livekit_end_of_turn_min_s: float = 0.0  # espera de LiveKit tras el fin de turno de AssemblyAI
    livekit_interruption_min_s: float = 0.5  # voz mínima del vecino para cortar al bot
    livekit_interruption_min_words: int = 0  # palabras mínimas para cortar al bot
    # El agente entra en la API con este secreto (cabecera Authorization: Bearer). Vacío = no
    agent_secret: str = ""
    agent_api_url: str = "http://127.0.0.1:8000"


settings = Settings()
