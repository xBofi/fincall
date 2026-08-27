# Backend

FastAPI + LangGraph + PostgreSQL. Ver `docs/superpowers/specs/2026-08-27-finca-configuration-and-graph-registry-design.md`.

```powershell
cd backend
uv sync
Copy-Item .env.example .env
uv run building-call            # API en http://127.0.0.1:8000 (docs en /docs)
uv run pytest
uv run ruff check src tests; uv run ruff format --check src tests
```

## Modos

- `BC_REPOSITORIO=memoria` (por defecto): datos de demostración en memoria, sin base de datos. Para validar los agentes.
- `BC_REPOSITORIO=postgres`: modo real. Requiere PostgreSQL y migraciones:

```powershell
uv run alembic upgrade head
uv run python -m building_call.repos.semilla   # datos de demostración
```

## Modelo

Solo Ollama local en esta fase (`qwen3.5:4b`). El cliente usa `format="json"` con `think=false`:
el esquema JSON estricto con este modelo/versión de Ollama cuelga la petición.

## Estructura

```text
src/building_call/
  domain/        configuración por capas (defaults -> administrador -> finca) y entidades
  graph/         estado, nodos reutilizables y registro de grafos (hoy solo `estandar`)
  llm/           contrato ModeloLenguaje y cliente Ollama
  repos/         Repositorio (protocolo), memoria, PostgreSQL, modelos SQL, semilla
  api/           FastAPI: /api/salud, /api/fincas, /api/llamadas, /api/incidencias
  servicios.py   GestorLlamadas: estado por llamada e invocación del grafo por turno
alembic/         migraciones
tests/           pytest con LLM falso determinista
```
