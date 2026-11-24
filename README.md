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

### Languages (Spanish and US English)

The agent speaks Spanish (es-ES) and US English (en-US) on one shared LangGraph graph. A language
is configuration, not code: `backend/src/building_call/i18n/locales/<code>.toml` holds its
providers and voices, the word lists of the deterministic paths (yes/no, set phrases, apartments,
spelled-out numbers), the translations of every template and the admin-facing phrases. Spanish is
the source language of the templates (`graph/templates.py`); the tests check that every other
language translates all of them with the same variables.

- `BC_LANGUAGE` sets the default language of a call; `POST /api/calls {"language": "en"}`, a
  `language` Media Streams parameter or the LiveKit job metadata choose another one per call.
- `CallManager` activates the call's language around each turn (`i18n.use`), so nodes,
  templates and parsers read it without threading it through every helper.
- Prompts: Spanish keeps its tuned prompt set; English uses the prompts ported from the US
  version (`llm/prompts_en.py`), measured with `evals/compare_models.py --language en`.
- Tests: `tests/` runs the flows in Spanish and `tests/en/` the same flows in English with the
  US demo buildings (`MemoryRepository("en")`).

### Calling the agent from the browser through Twilio

The console can call the agent over the phone network without a phone number: the Twilio Voice
JavaScript SDK (WebRTC) dials a TwiML App whose voice URL is the same webhook as the number, so
the call goes through `<Connect><Stream>`, μ-law 8 kHz, the server TTS, barge-in and the delivery
ledger exactly like a real call. The console opens the call first and shows it live.

1. In Twilio: an API key (Standard) for the Access Tokens. Keep the SID and secret in
   SecretBroker (`BC_TWILIO_API_KEY_SID`, `BC_TWILIO_API_KEY_SECRET`).
2. A public https URL for the API (`BC_PUBLIC_URL`, e.g. the ngrok domain) and
   `BC_TELEPHONY=twilio` with the account SID and auth token.
3. `uv run python -m building_call.telephony.configure --twiml-app` creates (or updates) the
   "FinCall console" TwiML App and prints `BC_TWILIO_TWIML_APP_SID`. It buys nothing.
4. In the console, choose **Twilio** as the voice mode and start the call. On a trial account
   Twilio plays a short notice first; the console presses the key for you.

Seguridad: contraseñas solo como hash scrypt; token de sesión opaco en cookie `HttpOnly` (`Secure` fuera de
localhost) con solo su hash en BD; sesiones revocables (logout, cambio de contraseña, desactivación, baja);
bloqueo temporal tras 5 intentos fallidos por usuario o IP (`BC_LOGIN_MAX_INTENTOS`, `BC_LOGIN_BLOQUEO_S`);
todo acceso y toda gestión de usuarios quedan en la auditoría. `BC_SESSION_SECRET` firma las URLs con las que
se descargan las grabaciones: es **obligatorio** (mínimo 32 caracteres) con `BC_REPOSITORY=postgres` y la API
se niega a arrancar sin él; genéralo con `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
