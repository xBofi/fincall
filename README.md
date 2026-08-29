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
uv run python -m building_call.repos.semilla  # fincas y administradores de demostración
uv run building-call                          # API en :8000 (lee backend/.env)
cd ../frontend; corepack pnpm run dev         # consola en :5173
```

Pila completa en contenedores: `docker compose up -d` (web en `http://localhost:8080`, Ollama sigue en el host).
Con `backend/.env` en `BC_REPOSITORIO=postgres` y `BC_DATABASE_URL=postgresql+asyncpg://building_call:<contraseña>@127.0.0.1:5433/building_call`.

### Acceso a la consola operativa

La consola es privada: usuarios internos con contraseña, sin proveedor externo de identidad.

1. Al arrancar la API por primera vez (sin usuarios en la base de datos) se crea el owner inicial
   `admin` / `admin` (o lo que indiquen `BC_ADMIN_INICIAL_USUARIO` / `BC_ADMIN_INICIAL_CONTRASENA` en `backend/.env`).
2. Entra en la consola con ese usuario: **te obligará a elegir una contraseña nueva** (mínimo 10 caracteres)
   antes de poder ver nada. Hasta entonces la API responde `403` en todo lo que no sea `/api/auth/*`.
3. En la pestaña **Usuarios** (solo rol `owner`) das de alta a más personas: cada alta o restablecimiento
   genera una contraseña temporal que se muestra una única vez y que el usuario debe cambiar al entrar.
   Desde ahí también cambias roles (`owner`, `administrador`, `calidad`), desactivas o eliminas usuarios.

### Telefonía

El asistente puede atender un número real (Twilio Media Streams; proveedor Provisional). Con las
credenciales y una URL https pública, `uv run python -m building_call.telefonia.configurar` deja el
número apuntando a la API. Guía completa en `docs/TELEFONIA.md`.

Seguridad: contraseñas solo como hash scrypt; token de sesión opaco en cookie `HttpOnly` (`Secure` fuera de
localhost) con solo su hash en BD; sesiones revocables (logout, cambio de contraseña, desactivación, baja);
bloqueo temporal tras 5 intentos fallidos por usuario o IP (`BC_LOGIN_MAX_INTENTOS`, `BC_LOGIN_BLOQUEO_S`);
todo acceso y toda gestión de usuarios quedan en la auditoría. `BC_SESION_SECRETO` firma las URLs de audio.
