"""Configuración de infraestructura y secretos.

Solo variables de entorno (`.env` en local, variables/secretos de CI en despliegue).
La configuración de negocio (fincas, especialistas, reglas) NO va aquí: vive en PostgreSQL.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BC_", env_file=".env", extra="ignore")

    # "memoria" permite validar los agentes sin base de datos; "postgres" es el modo real.
    repository: str = "memory"

    # Default language of a call (ISO 639-1, one of `building_call/i18n/locales/*.toml`). A call
    # can pick another one (Twilio number, stream parameter, console) or switch after detection.
    language: str = "es"
    # Languages this deployment answers in (codes of the registry); empty = all of them
    languages: list[str] = []
    # Detect the caller's language in the first turns of a call whose language was not chosen
    # explicitly, and switch when the evidence is at least this confident (0-1)
    language_detection: bool = True
    language_detection_threshold: float = 0.6
    language_detection_turns: int = 2
    # Voice persona per client (`building_call/i18n/personas/<id>.toml`); empty = none
    persona: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/building_call"

    # LLM: "auto" (carrera si hay credenciales de Cloudflare, si no ollama) | "ollama" | "workers_ai" |
    # "vertex" (Gemini en Vertex AI, solo Europa) | "openai" | "bedrock" (AWS, solo Europa) |
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

    # OpenAI (BC_LLM=openai): Responses API with Structured Outputs (strict JSON schema).
    # Model and prices from developers.openai.com (2026-11-24). EU data residency needs an
    # eligible project and the regional host: BC_OPENAI_URL=https://eu.api.openai.com
    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"
    openai_url: str = "https://api.openai.com"
    # Reasoning effort sent as `reasoning.effort`; empty = the model's default
    openai_reasoning_effort: str = ""
    openai_timeout_s: float = 20.0

    # AWS Bedrock (BC_LLM=bedrock): Converse API with a Bedrock API key (Bearer) and structured
    # output. Region SOLO europea (validated); the EU inference profile keeps data in the EU
    bedrock_api_key: str = ""
    bedrock_region: str = "eu-west-1"
    bedrock_model: str = "eu.anthropic.claude-haiku-5-5"
    bedrock_timeout_s: float = 20.0

    # Hybrid routing (nlp/): the Hugging Face turn classifier answers first and the LLM is only
    # called below the threshold. Off until evals/compare_nlp.py proves it; needs the `nlp` extra
    # and the weights from nlp/train_classifier.py
    nlp_hybrid: bool = False
    nlp_model_dir: str = "nlp/models/turn-classifier"
    # 0.8: the lowest with no accuracy loss vs LLM-only on both gold sets (evals/compare_nlp.py)
    nlp_threshold: float = 0.8

    # STT por lotes en servidor: "none" (el navegador transcribe) | "whisper" (faster-whisper local)
    stt: str = "none"
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
    # none | twilio | vonage | "twilio,vonage" (several carriers can be live at once)
    telephony: str = "none"
    public_url: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_number: str = ""  # E.164, p. ej. +34912345678
    # Calls from the web console through Twilio (Voice JS SDK): an API key signs the browser's
    # Access Token, and the TwiML App sends the call to the same voice webhook
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_twiml_app_sid: str = ""
    # Called number (E.164) -> language / persona, for numbers that serve one language or client:
    # BC_TWILIO_NUMBER_LANGUAGES='{"+15550100000": "en"}'
    twilio_number_languages: dict[str, str] = {}
    twilio_number_personas: dict[str, str] = {}
    telephony_bargein_words: int = 2
    # Vonage Voice API (adapter built from the docs, NOT tested live: no account yet). The
    # signature secret verifies the signed answer/event webhooks (JWT HS256).
    vonage_signature_secret: str = ""
    vonage_number: str = ""  # E.164, informative

    # n8n (or any automation endpoint). Outgoing call events: signed webhooks (HMAC-SHA256 over
    # "<timestamp>.<body>"), retries with backoff, Idempotency-Key. Empty URL = off.
    n8n_webhook_url: str = ""
    n8n_webhook_secret: str = ""  # shared with the receiver; SecretBroker id bc-n8n-webhook-secret
    n8n_webhook_events: str = ""  # comma list of event types; empty = all
    n8n_webhook_timeout_s: float = 5.0
    n8n_webhook_attempts: int = 4
    # Tool the agent calls during the call (`book_visit` node). Empty URL = no tool node. The
    # timeout is spent inside the caller's last turn: keep it short and measure response_ms.
    n8n_tool_url: str = ""
    n8n_tool_timeout_s: float = 1.5
    # TTS de servidor (solo telefonía; la consola web habla con el navegador)
    tts: str = "auto"  # auto | elevenlabs | piper | workers_ai | none
    # ElevenLabs (BC_TTS=elevenlabs). Sin residencia UE salvo plan Enterprise (BC_ELEVENLABS_URL).
    elevenlabs_api_key: str = ""
    elevenlabs_voice: str = ""  # voice ID, no el nombre
    # Medido 2026-10-11: v4 Turbo primer audio ~158 ms (como Flash v2.5) con mejor voz
    elevenlabs_model: str = "eleven_v4_turbo"
    elevenlabs_url: str = "https://api.elevenlabs.io"
    piper_bin: str = "piper"
    # One Piper voice per language: <dir>/<piper_voice>.onnx (+ .onnx.json), names in the
    # locale files (es_ES-davefx-medium, en_US-lessac-medium, ca_ES-upc_ona-medium, ...)
    piper_dir: str = ""
    piper_model: str = ""  # ruta al .onnx (junto a su .onnx.json), p. ej. es_ES-davefx-medium
    cloudflare_tts_model: str = "@cf/myshell-ai/melotts"

    # Audio de llamadas: directorio privado, URL firmada de corta duración, retención
    audio_dir: str = ".audio"
    audio_retention_days: int = 14
    # Audit log (changes and accesses): two years, Confirmed by Andreu as DPO (2026-10-22)
    audit_retention_days: int = 730
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
    # Entrada por SIP (spec 2026-10-22-telnyx-sip-pilot-design, `telephony/sip.py`). El número
    # al que llaman (en local uno ficticio); quién puede entregar llamadas: usuario y contraseña
    # (softphone) o IPs/redes separadas por comas (operador). Sin ninguno no se configura.
    sip_number: str = ""
    sip_auth_username: str = ""
    sip_auth_password: str = ""
    sip_allowed_addresses: str = ""
    # A quién pasar la llamada cuando el vecino pide una persona (tel:+34… o sip:…). Vacío:
    # se cuelga y el caso queda para el administrador, como en la consola
    handoff_target: str = ""
    # El agente entra en la API con este secreto (cabecera Authorization: Bearer). Vacío = no
    agent_secret: str = ""
    agent_api_url: str = "http://127.0.0.1:8000"


settings = Settings()
