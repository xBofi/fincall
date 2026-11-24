# Finca Configuration and Graph Registry Design

## Goal

Define how Building Call Assistant models per-finca behaviour so that a new finca with a different configuration can be onboarded without touching the call flow code, while allowing a finca to run a different LangGraph graph when its process genuinely differs.

Status legend follows the vault convention: **Confirmado** (stated by the product owner), **Provisional** (proposed), **Pendiente** (requires external input).

## Decisions

### Graph registry, not a fixed graph — Confirmado

Every finca configuration references a graph by name (`grafo`). A registry maps names to compiled LangGraph graphs. Today a single graph `estandar` exists and every finca uses it. When a real finca needs a different process, a new graph is registered under a behavioural name (for example `con_horario_nocturno`), never under a finca name, and only that finca references it.

Rules:

- Nodes are plain functions independent of any graph; a new graph recombines existing nodes and adds only what differs.
- No graph is created before a real finca needs it.
- Graph selection is data (the finca's `grafo` field), never a conditional inside a node.
- The LLM never chooses the graph or the next node; edges do (see `04 Arquitectura/Arquitectura del voicebot` in the vault).

### Specialists belong to the administrator — Confirmado

Specialists are defined at administrator level. A finca maps problem categories to specialist ids of its administrator, or inherits the administrator's default mapping.

### Layered configuration — Provisional

```text
Defaults globales (código, modelos Pydantic)
  └── Administrador  (sobrescribe lo que declare)
        └── Finca    (sobrescribe lo que declare)
```

The effective configuration is resolved once when a call starts and is placed in the graph state. Every field is optional at the administrator and finca levels; absent fields inherit.

Initial fields (the real set of variations between fincas is **Pendiente**):

| Campo | Nivel mínimo | Descripción |
|---|---|---|
| `grafo` | finca (default `estandar`) | Grafo registrado a ejecutar |
| `catalogo_categorias` | administrador | Catálogo cerrado que se inyecta como `enum` en el esquema JSON del LLM |
| `especialista_por_categoria` | administrador, finca sobrescribe | Mapa categoría → especialista |
| `reglas_aviso` | administrador, finca sobrescribe | Urgencia → canal (`correo`, `llamada`, `ticket`) → destino |
| `contacto` | finca | Correo y teléfono oficial de la finca |
| `intentos_finca`, `intentos_vivienda` | defaults (3, 3) | Reintentos de identificación |
| `emergencias_activas` | defaults (`false`) | Las emergencias se tratan como `alta` mientras sea `false`. Con `true` (2026-10-22): nodo `atender_emergencia` con indicación de seguridad (112; gas: no encender luces ni aparatos, salir y ventilar), confirmación mínima (finca y emergencia), sin detalle ni resumen largo; incidencia `emergencia` y aviso por llamada al teléfono oficial de la finca + correo al coordinador **simulados** |

### PostgreSQL from day one — Confirmado

There is no file-based configuration phase. Business configuration lives in PostgreSQL, managed with SQLAlchemy and Alembic from the first migration.

Configuration documents are stored as JSONB validated by the Pydantic models on write. Each change inserts a new row with `valido_desde`; rows are never updated in place. A call records the configuration version it was decided with, satisfying the audit requirements of the architecture note.

Provisional tables:

```text
administradores(id, nombre)
especialistas(id, administrador_id, nombre, categoria_principal, canal_contacto, destino)
fincas(id, administrador_id, direccion, correo, telefono_oficial)
viviendas(id, finca_id, portal, piso, puerta)
configuraciones(id, ambito ∈ {administrador, finca}, ambito_id, documento JSONB, valido_desde)
llamadas(id, iniciada_en, telefono_llamante, finca_id, vivienda_id, nombre_declarado,
         transcripcion, resumen, resultado, motivo_revision, configuracion_id, grafo)
incidencias(id, llamada_id, finca_id, vivienda_id, descripcion, categoria,
            especialista_id, urgencia, estado, creada_en)
incidencia_historial(id, incidencia_id, estado_anterior, estado_nuevo, cambiado_en, nota)
```

### Secrets versus business configuration — Confirmado

- Secrets and infrastructure settings (database URL, model provider keys, Seq/Langfuse/GlitchTip endpoints) are injected through environment variables: `.env` locally, CI/CD environment variables and secrets in deployment. They never enter Git.
- Business configuration (administrators, fincas, specialists, categories, notification rules) lives in PostgreSQL, never in CI variables or `.env`.

### Prompts — Provisional

One prompt per node, shared by all fincas. Finca-specific values (category catalogue, contact data) are interpolated as data. Prompt text is versioned in Langfuse once the observability platform exists; until then it lives in source with an explicit version constant.

## Development and validation phase — Confirmado

The first development increment must let the product owner validate that the agents work correctly before any paid API is used:

- LLM: local Ollama (`qwen3.5:4b` by default, `qwen3:8b` for quality checks). LiveKit, paid voice providers and paid LLM APIs are not used in this phase. LiteLLM is likewise deferred until a paid provider is introduced.
- Speech: the browser Web Speech API (SpeechRecognition for STT, SpeechSynthesis for TTS). No paid voice provider.
- Frontend: a React + TypeScript + Vite + Ant Design console that starts a simulated call, streams the conversation, shows the current graph node, the structured extraction, the validations applied and the resulting incident, and allows text input as a fallback when the microphone is unavailable.
- Backend: FastAPI exposing the call session API used by the console; the same API will later be driven by the telephony integration.

## Out of scope for this document

Telephony provider and real-time media layer (LiveKit is the candidate, self-hosted preferred), real STT/TTS vendors, LiteLLM routing, Langfuse prompt migration and the observability platform are covered by other specifications or are pending.

## Success Criteria

- A new finca that behaves like the others is onboarded by inserting rows, with no code change.
- A finca that needs a different process is served by registering one graph and setting its `grafo` field.
- Every stored call references the configuration version and graph used.
- Changing a global default propagates to every finca that did not override it.
- The product owner can complete a simulated call end to end in the browser with local models only.
