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

## Modelo y latencia

Solo Ollama local en esta fase (`qwen3.5:4b`: 96 % de exactitud en interpretación, ~600-700 ms).
El cliente usa `format="json"` con `think=false`: el esquema JSON estricto con este modelo/versión
de Ollama cuelga la petición. Comparar modelos y formatos de prompt:

```powershell
uv run python evals/comparar_modelos.py --modelos qwen3.5:4b,qwen3:1.7b --verboso
```

Casos reales al banco: en el Historial de la consola, calidad (o administrador/owner) marca un
turno del vecino como "caso para el banco" con lo que el bot debería haber entendido. Se pasan a
`evals/gold_*.jsonl` sin duplicar (solo el texto del turno y la pregunta del bot, nunca teléfono
ni nombre del vecino):

```powershell
uv run python evals/exportar_casos.py --simular        # desde la base de datos configurada
uv run python evals/exportar_casos.py --entrada casos.json   # JSON de GET /api/operaciones/casos
```

Métricas de latencia por etapa: `GET /api/metricas`. El navegador envía `respuesta_ms` (fin de
voz del vecino → inicio de voz del bot) a `POST /api/llamadas/{id}/metricas`. Umbrales en
`metricas.py`. Spec: `docs/superpowers/specs/2026-08-28-voice-pipeline-latency-design.md`.

## STT en servidor (opcional)

`BC_STT=whisper` activa faster-whisper (`BC_WHISPER_MODELO=small|base|turbo`, `BC_WHISPER_DISPOSITIVO=cuda|cpu`).
Instalar con `uv pip install faster-whisper` (opcional, no está en el lock). Sin él, el navegador transcribe (Web Speech API).

## Estructura

```text
src/building_call/
  domain/        configuración por capas (defaults -> administrador -> finca) y entidades
  graph/         estado, nodos reutilizables, `siguiente_paso` y registro de grafos (hoy solo `estandar`)
  llm/           contrato ModeloLenguaje, cliente Ollama, interpretación de turno y prompts versionados
  metricas.py    tiempos por turno, agregados p50/p95 y calificación por umbrales
  stt.py         reconocimiento de voz en servidor (faster-whisper, opcional)
  repos/         Repositorio (protocolo), memoria, PostgreSQL, modelos SQL, semilla
  api/           FastAPI: /api/salud, /api/fincas, /api/llamadas, /api/incidencias
  servicios.py   GestorLlamadas: estado por llamada e invocación del grafo por turno
alembic/         migraciones
evals/           gold sets y comparativa de modelos (exactitud + latencia)
tests/           pytest con LLM falso determinista (flujo, naturalidad, API, métricas)
```
