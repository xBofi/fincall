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

### Acceso a la consola operativa con Google

1. En [Google Cloud Console](https://console.cloud.google.com/) crea (o elige) un proyecto.
2. **APIs y servicios → Pantalla de consentimiento OAuth**: tipo *Externo*, rellena nombre y correo de contacto; en *Usuarios de prueba* añade los correos que entrarán (mientras la app esté en modo prueba solo entran ellos).
3. **APIs y servicios → Credenciales → Crear credenciales → ID de cliente de OAuth**, tipo *Aplicación web*.
   - Orígenes autorizados: `http://localhost:5173` y `http://localhost:8000`.
   - URI de redirección autorizada: `http://localhost:8000/api/auth/callback`.
4. Google muestra el *ID de cliente* y el *secreto de cliente*. Cópialos solo en `backend/.env` (nunca en chat, notas ni commits):

```dotenv
BC_AUTH_MODO=google
BC_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
BC_GOOGLE_CLIENT_SECRET=...
BC_GOOGLE_REDIRECT_URL=http://localhost:8000/api/auth/callback
BC_ACCESO_OWNERS=tu-correo@dominio.es          # allowlist por rol, separados por comas
BC_ACCESO_ADMINISTRADORES=
BC_ACCESO_CALIDAD=
BC_SESION_SECRETO=<cadena aleatoria larga>       # p. ej. python -c "import secrets; print(secrets.token_urlsafe(48))"
```

5. Reinicia la API y entra desde la consola con **Entrar con Google**. `GET /api/salud` indica `acceso: google`.
   Sin secreto o sin allowlist el acceso queda deshabilitado; `BC_AUTH_MODO=desarrollo` solo sirve en local.
