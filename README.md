# Building Call Assistant

MCP local para que Codex y Claude consulten y editen la bóveda de Obsidian del proyecto.

## Puesta en marcha

```powershell
python -m pip install -e .\mcp_server
gh auth status
.\scripts\start-mcp-codex.ps1
```

Configura `config/codex-mcp.toml` en Codex y `config/claude-mcp.json` en Claude. Ambos usan el mismo proceso MCP y la misma bóveda. El transporte es `stdio`: el cliente lo levanta bajo demanda y lo termina al cerrar la sesión.

## RAG local y GraphRAG

El MCP incluye `rebuild_rag_index`, `rag_search` y `rag_context`. El índice SQLite FTS5 se guarda en `%LOCALAPPDATA%\BuildingCallAssistant\rag.sqlite3`, fuera de la bóveda y de Git. Se actualiza incrementalmente antes de buscar y puede reconstruirse con:

```powershell
.\scripts\rebuild-rag-index.ps1
```

Para recuperación semántica y relacional, el MCP también ofrece `rebuild_graph_index`, `hybrid_search`, `graph_context`, `graph_inspect` y `evaluate_rag`. Usa únicamente Ollama local: `mxbai-embed-large:latest` para embeddings y `qwen3.5:4b` para extraer relaciones con evidencia literal. El modo `quality` usa `qwen3:8b`.

```powershell
.\scripts\rebuild-graphrag-index.ps1
```

El índice híbrido fusiona FTS5 y vectores, deduplica por documento y conserva cada relación con su fragmento de evidencia. Las comunidades son componentes conectados deterministas; no se generan resúmenes inventados. Evalúa el retrieval contra el conjunto versionado en `mcp_server/evals/graphrag_gold.jsonl` mediante `evaluate_rag`.

## Git de la bóveda

La rama de trabajo de ambos clientes es `bofi`. El sincronizador sube los cambios y promociona automáticamente:

```text
bofi -> develop -> main
```

Ejecuta `scripts/sync-vault.ps1 -DryRun` para validar sin mutar GitHub. Los conflictos detienen la operación; no se usa `force-push` ni `reset --hard`.

## Autenticación

Usa `gh auth login` con un token fine-grained limitado al repositorio `<private-vault-repo>`, con `Contents: Read and write`, `Pull requests: Read and write` y `Metadata: Read-only`. La clave se guarda en el keyring de GitHub CLI, no en este repositorio.

## Cómo enruta LangGraph una llamada (vista de enrutado)

![Traza en directo de una llamada real](docs/images/routing/live-trace.gif)

La pantalla **Flujo** (y la columna derecha de **Simular llamada**) dibuja el grafo de LangGraph tal
como está compilado y enseña, nodo a nodo, por qué la llamada fue a cada sitio.

- **Estructura**: `GET /api/graph/structure` exporta los nodos de `compiled.get_graph()` y las rutas
  del manifiesto con su condición, verificadas contra las aristas condicionales compiladas
  (`drift` vacío o la vista lo avisa). Disposición automática con ELK; ninguna coordenada a mano.
- **En directo**: `GET /api/calls/{id}/events` (SSE) emite `node_enter`, `node_exit` y `turn_end`.
  Cada visita (`backend/src/building_call/graph/tracing.py`) lleva el tiempo del nodo, el salto,
  la condición declarada que se cumplió, la intención y si se interpretó con o sin modelo, las
  llamadas y el tiempo del LLM, validaciones y un diff de estado sin datos personales. Tokens y
  coste se muestran como «no medido»: el proveedor no los devuelve en este flujo.
- **Repetir llamada**: cualquier llamada guardada, paso a paso (← → Inicio Fin Espacio), con el
  turno de la conversación de cada visita y el diff de estado o el estado acumulado.
- **Demo**: `http://localhost:5173/demo` reproduce una llamada grabada a través del grafo real,
  sin sesión, backend ni telefonía (`?theme=dark`, `?lang=en|ca|ar|hi|ur`). Se vuelve a grabar con
  `cd backend; uv run python evals/record_demo_call.py --model qwen3.5:latest`; un test falla si
  el grafo cambia y la grabación no.

| Claro | Oscuro |
| --- | --- |
| ![Demo, tema claro](docs/images/routing/demo-light.png) | ![Demo, tema oscuro](docs/images/routing/demo-dark.png) |
| ![Repetición con diff de estado](docs/images/routing/replay-state-light.png) | ![Árabe, RTL](docs/images/routing/demo-rtl.png) |
| ![Urdu, RTL](docs/images/routing/demo-rtl-ur-light.png) | ![Simular llamada con el grafo en vivo](docs/images/routing/simulate-live-light.png) |

La vista está en los seis idiomas de la consola (es, en, ca, ar, hi, ur; RTL en árabe y urdu): textos
con claves `routing.*`/`flow.*` en `frontend/src/i18n/messages/`, y títulos de nodo y condiciones de
ruta traducidos por id (`routing.graph.*`). El resumen de cada nodo se queda en el idioma del código.

Tests: `cd backend; uv run --extra voice pytest tests/test_routing_view.py` y `cd frontend; corepack pnpm run test`.

## Arrancar la aplicación en local

```powershell
docker compose up -d db                       # PostgreSQL 17 (127.0.0.1:5433, datos en volumen)
cd backend
uv run alembic upgrade head                   # esquema
uv run python -m building_call.repos.seed  # fincas y administradores de demostración
uv run building-call                          # API en :8000 (lee backend/.env)
cd ../frontend; corepack pnpm run dev         # consola en :5173
```

Pila completa en contenedores: `docker compose up -d` (web en `http://localhost:8080`, Ollama sigue en el host).
Con `backend/.env` en `BC_REPOSITORY=postgres` y `BC_DATABASE_URL=postgresql+asyncpg://building_call:<contraseña>@127.0.0.1:5433/building_call`.

### Acceso a la consola operativa

La consola es privada: usuarios internos con contraseña, sin proveedor externo de identidad.

1. Al arrancar la API por primera vez (sin usuarios en la base de datos) se crea el owner inicial
   `admin` / `admin` (o lo que indiquen `BC_ADMIN_INICIAL_USUARIO` / `BC_ADMIN_INICIAL_CONTRASENA` en `backend/.env`).
2. Entra en la consola con ese usuario: **te obligará a elegir una contraseña nueva** (mínimo 10 caracteres)
   antes de poder ver nada. Hasta entonces la API responde `403` en todo lo que no sea `/api/auth/*`.
3. En la pestaña **Usuarios** (solo rol `owner`) das de alta a más personas: cada alta o restablecimiento
   genera una contraseña temporal que se muestra una única vez y que el usuario debe cambiar al entrar.
   Desde ahí también cambias roles (`owner`, `administrator`, `quality`), desactivas o eliminas usuarios.

### Usar la consola fuera de casa (túnel ngrok)

La consola no se despliega en ningún sitio todavía: para usarla desde fuera se publica el servidor
de desarrollo por un túnel https. Se expone **el 5173**, no la API: Vite ya hace de proxy de `/api`
y del WebSocket de voz, así que todo viaja por un único origen y no hacen falta ni CORS ni cambios
en la cookie. El micrófono, además, solo funciona en contexto seguro, y el túnel da https.

```powershell
cd frontend; $env:BC_TUNEL=1; corepack pnpm run dev    # atiende en 127.0.0.1:5173
ngrok http 127.0.0.1:5173                              # en otra terminal
```

`BC_TUNEL=1` hace dos cosas: atender también en IPv4 (por defecto Vite solo escucha en `::1` y el
túnel no llegaría) y apuntar el cliente de HMR al 443 público. El Host del túnel ya está en
`allowedHosts`; sin eso Vite responde *Blocked request*.

La URL que imprime ngrok es **pública**: la protege solo el usuario y la contraseña de la consola,
y al otro lado hay grabaciones y datos de vecinos. Úsala mientras la necesites y corta el túnel
después; con el plan gratuito la URL cambia en cada arranque. La primera visita muestra el aviso
de ngrok: hay que pulsar *Visit Site* una vez para que las peticiones de la aplicación pasen.

### Telefonía

El asistente puede atender un número real (Twilio Media Streams; proveedor Provisional). Con las
credenciales y una URL https pública, `uv run python -m building_call.telephony.configure` deja el
número apuntando a la API. Guía completa en `docs/TELEFONIA.md`.

### Multilingual architecture

The agent speaks **Spanish (es-ES), US English (en-US), Catalan (ca-ES), Arabic (ar-AE), Hindi
(hi-IN) and Urdu (ur-PK)** on one shared LangGraph graph. A language is configuration, not code.

```text
 caller audio ─▶ STT (AssemblyAI EU, language_codes) ──text──▶ CallManager
                    ▲  UpdateConfiguration on switch               │ i18n.use(call language)
                    │                                              │ personas.use(client persona)
                    │                                              ▼
            detection ◀── first turns: script + function words ── graph (shared nodes)
            (+ AssemblyAI's language_code / confidence)            │  parser_for(lang): yes/no,
                                                                   │  apartments, set phrases
                                                                   │  render(id): template in lang
                                                                   │  LLM prompt set: es | en+note
                                                                   ▼
 caller ear ◀── TTS chain per language (ElevenLabs → Piper → MeloTTS) ◀── reply text
```

- **One file per language**: `backend/src/building_call/i18n/locales/<code>.toml` holds the
  locale, RTL flag, STT/TTS settings and voices, the word lists of the deterministic paths
  (yes/no, refusals, set phrases, spelled-out numbers, floors and apartment words, gas/fire),
  the translations of every template, FAQ answers, detail questions and admin-facing phrases.
  Adding a 7th language means adding one file: the tests check it translates every template
  with the same variables and question marks.
- **Rules no word list can express** live in `i18n/parsing.py` (`Parser`, generic and lexicon
  driven; `SpanishParser` keeps the rules tuned on real Spanish calls). Formats (dates, numbers,
  currency, ordinals, lists) come from CLDR via Babel (`i18n/formatting.py`).
- **Choosing the language of a call**: explicit (`POST /api/calls {"language"}`, the console,
  a `language` Media Streams parameter, `BC_TWILIO_NUMBER_LANGUAGES` per called number), else
  `BC_LANGUAGE` plus **detection** of the caller's first turns (`i18n/detection.py`: script,
  Arabic vs Urdu letters and words, distinctive function words for Latin script, AssemblyAI's
  reported language when it is sure). It switches only above `BC_LANGUAGE_DETECTION_THRESHOLD`
  (default 0.6), otherwise the call stays in the default language. Asking by name ("English,
  please") switches at any time and the current question is asked again in that language.
- **Mid-call switch**: STT is steered with AssemblyAI `UpdateConfiguration` (no reconnect), TTS
  changes `language_code`/voice per sentence; the phone bridge, the console WebSocket and the
  LiveKit agent all follow it.
- **Prompts**: Spanish keeps its measured prompt set; every other language uses the English
  prompts (what models follow best) plus a note asking for values in the caller's language.
- **Client personas** (`i18n/personas/<id>.toml`): agent name, ElevenLabs voice, tone for the
  phrasing model, greeting and FAQ answers per language; chosen per number
  (`BC_TWILIO_NUMBER_PERSONAS`), per call or by `BC_PERSONA`.
- **Console**: six UI languages, right-to-left for Arabic and Urdu (`frontend/src/i18n`).

#### Providers per language

Checked against the providers' current documentation (November 2026).

| Language | STT (streaming) | Server TTS | Local TTS (dev) | Twilio `<Say>` fallback | LLM |
|---|---|---|---|---|---|
| es-ES | AssemblyAI Universal-Streaming (EU) | ElevenLabs `eleven_v4_turbo` | Piper `es_ES-davefx-medium`, MeloTTS | `Polly.Lucia-Neural` | Gemini (Vertex EU) / Ollama |
| en-US | AssemblyAI (EU) | ElevenLabs | Piper `en_US-lessac-medium`, MeloTTS | `Polly.Joanna-Neural` | Gemini / Ollama |
| ca-ES | AssemblyAI (EU) | ElevenLabs (v3/v4 only: Flash v2.5 has no Catalan) | Piper `ca_ES-upc_ona-medium` | `Polly.Arlet-Neural` | Gemini |
| ar-AE | AssemblyAI (EU) | ElevenLabs | Piper `ar_JO-kareem-medium` | `Polly.Hala-Neural` | Gemini |
| hi-IN | AssemblyAI (EU) | ElevenLabs | Piper `hi_IN-pratham-medium` | `Polly.Kajal-Neural` | Gemini |
| ur-PK | AssemblyAI (EU) | ElevenLabs (v3/v4 only) | **none** (no Piper voice) | **none**: falls back to `BC_LANGUAGE`'s voice | Gemini |

Fallbacks: the server TTS is a per-language chain (`FallbackSynthesizer`): the configured
provider first, then the next available one that speaks the language; Urdu has no local voice,
so without ElevenLabs it has no server voice (the console then uses the browser's voice, if the
OS has an Urdu one). If AssemblyAI is unavailable, the console falls back to the browser's
speech recognition (`browser_lang`); phone calls need AssemblyAI. Local Ollama models are weak
in Urdu and Hindi: use Gemini for those languages in production.

### Run the demo

One script starts everything for a browser call through Twilio: the API with in-memory data
(no PostgreSQL), the console behind ngrok, and Twilio browser calls.

1. Once: `backend/.secretbroker` maps `BC_TWILIO_ACCOUNT_SID`, `BC_TWILIO_AUTH_TOKEN`,
   `BC_TWILIO_API_KEY_SID` and `BC_TWILIO_API_KEY_SECRET` (names only, no values), and
   `BC_TWILIO_TWIML_APP_SID`, `BC_OWNER_EMAIL` and the voice settings are in `backend/.env`.
2. Give it your fixed ngrok domain, then run it:

   ```powershell
   $env:BC_PUBLIC_URL = "https://<your-domain>.ngrok-free.dev"   # or -PublicUrl, or a SecretBroker config entry
   .\scripts\start-demo.ps1
   ```

   It prints the URL to open once `/api/health` answers through the tunnel. Ctrl+C stops the
   API, the console and ngrok. Logs are in `%TEMP%\fincall-demo`.
3. Open the URL, click *Visit Site* on the ngrok notice, and sign in with Google or Microsoft.
4. In **Simulate a call**, pick the voice mode **Twilio**, choose the call language (or
   *Detect automatically*), and press **Start call**.

Data lives in memory and is gone when the script stops. A browser call costs Twilio's Client
rate per minute.

### Talking to the agent from the browser, as a phone caller

Two ways to test the **phone path** (μ-law 8 kHz, server STT and TTS, barge-in, delivery
ledger, two-track recording) without a phone number. Both are voice modes of the console's
simulator, and the console follows the call live in either.

**1. Phone line (no Twilio): `Phone line` mode.** The browser captures the microphone at 8 kHz,
encodes G.711 μ-law in 20 ms frames and sends them over `WS /api/telephony/browser/line`, which
speaks the Twilio Media Streams protocol (`start`/`media`/`mark`/`clear`/`stop`) and plugs into
the same `CallBridge` as a phone call (`telephony/browser_line.py`, `frontend/src/phoneLine.ts`).
It needs only the console session, `BC_ASSEMBLYAI_API_KEY` and a server voice (`BC_TTS`): no
Twilio account, no public URL, no cost beyond STT/TTS.

**2. Twilio Voice JS SDK (WebRTC): `Twilio` mode.** The browser gets a 15-minute Access Token
(`POST /api/telephony/twilio/browser-token`, identity `console_<user>`) and dials a TwiML App
whose voice URL is the same webhook as the phone number; Twilio then opens the Media Stream to
the bridge. The real carrier path, still with no phone number. Setup, once:

1. Twilio credentials in SecretBroker (tag `fincall`): account SID and auth token
   (`BC_TWILIO_ACCOUNT_SID`, `BC_TWILIO_AUTH_TOKEN`), `BC_TELEPHONY=twilio`.
2. A **Standard API key** created in the Twilio console (Account → API keys); store its SID and
   secret as `BC_TWILIO_API_KEY_SID` / `BC_TWILIO_API_KEY_SECRET` (Access Tokens are signed with it).
3. A public https URL for the API (`BC_PUBLIC_URL`, e.g. the fixed ngrok domain) so Twilio can
   reach the webhook and the Media Stream.
4. `uv run python -m building_call.telephony.configure --twiml-app` creates or updates the
   "FinCall console" TwiML App (voice URL = the webhook) and prints `BC_TWILIO_TWIML_APP_SID`.
   It creates nothing else and buys nothing; browser (Client) minutes are billed by Twilio at its
   Client rate when a call is made.
5. Restart the API: `GET /api/health` shows `twilio_browser: true` and the console enables the
   mode. On a trial account Twilio plays a short notice first; the console presses the key.

Seguridad: contraseñas solo como hash scrypt; token de sesión opaco en cookie `HttpOnly` (`Secure` fuera de
localhost) con solo su hash en BD; sesiones revocables (logout, cambio de contraseña, desactivación, baja);
bloqueo temporal tras 5 intentos fallidos por usuario o IP (`BC_LOGIN_MAX_INTENTOS`, `BC_LOGIN_BLOQUEO_S`);
todo acceso y toda gestión de usuarios quedan en la auditoría. `BC_SESSION_SECRET` firma las URLs con las que
se descargan las grabaciones: es **obligatorio** (mínimo 32 caracteres) con `BC_REPOSITORY=postgres` y la API
se niega a arrancar sin él; genéralo con `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
