# Consola: grafo del bot, plantillas editables y catálogos

**Fecha:** 2026-09-13 · **Estado:** Implementado el 2026-09-13 (Provisional hasta validarlo en uso)
**Repo:** app (`develop`), carpetas `backend/` y `frontend/`

## 1. Problema

La consola operativa muestra hoy llamadas, latencia y usuarios, pero el comportamiento del
asistente es opaco: para saber qué pregunta el bot, en qué orden y por qué, hay que leer
`graph/nodos.py` (1 802 líneas). Tampoco hay forma de ajustar lo que se le dice al vecino sin
tocar código y desplegar, ni de consultar qué fincas y profesionales hay dados de alta.

Se quieren tres cosas desde la propia web:

1. Ver el grafo completo del bot con sus uniones y un resumen de lo que hace cada bloque.
2. Editar el texto de cada bloque del flujo, cuando tenga sentido.
3. Consultar la base de datos de profesionales y fincas con su información.

## 2. Hallazgos que condicionan el diseño

### 2.1 El grafo no tiene aristas estáticas

`graph/registro.py:56-60` conecta **todos los nodos con todos**:

```python
destinos = {**{n: n for n in _ORDEN_ESTANDAR}, END: END}
for nombre in _ORDEN_ESTANDAR:
    g.add_conditional_edges(nombre, _siguiente, destinos)
```

`_siguiente` solo lee `estado["nodo_actual"]`. Dibujar lo que LangGraph tiene compilado produce
un grafo completo de 18×18, sin valor. El flujo real vive en `siguiente_paso`
(`graph/nodos.py:783`) y en los `_avanzar` / `_preguntar` de cada nodo.

**Consecuencia:** la estructura hay que declararla aparte, y una declaración no verificada se
desincroniza. Se declara **y se verifica en ejecución**.

### 2.2 No hay un prompt por bloque: hay tres prompts y muchas plantillas

| Prompt de sistema | Fichero | Versión |
|---|---|---|
| Interpretación del turno | `llm/interpretacion.py:97` | `interpretacion-v3` |
| Clasificación | `llm/prompts.py:15` | `clasificacion-v2` |
| Redacción | `llm/redaccion.py:19` | `redaccion-v1` |

Lo que cada nodo tiene es una **plantilla**: texto fijo con datos interpolados
(`f"He identificado {finca.direccion}. ¿Es correcto?"`). Esa plantilla es el *MENSAJE
OBLIGATORIO* que el redactor parafrasea conservando los hechos.

### 2.3 Los prompts de sistema tienen contratos duros con el código

- El de interpretación **declara las claves JSON** que `normalizar_interpretacion` mapea y
  `Interpretacion` valida. Renombrar o quitar una clave hace que el dato deje de extraerse **en
  silencio**, sin error.
- Los enums (`INTENCIONES`, `URGENCIAS`) se validan en código; el catálogo de categorías se
  **inyecta** desde `ConfiguracionEfectiva`, no está escrito en el prompt.
- El bloque `FORMATO` es latencia medida: claves abreviadas bajan la exactitud del 96% al 82%,
  y los nulos y saltos de línea triplican la latencia.
- El de redacción tiene un contrato ("termina con la misma pregunta", "conserva los hechos") que
  `conserva_hechos()` verifica en código. Romperlo no da error: cae al 100% en plantilla y solo
  se detecta por la métrica `redaccion: plantilla:hechos_no_conservados`.

**Consecuencia:** los tres prompts de sistema **no se editan** en esta entrega.

### 2.4 Hay caminos que no pasan por el modelo

Sí/no, `"3º B"`, nombre simple, saludos, relleno y FAQ se resuelven en código
(`_respuesta_si_no`, `_extraer_vivienda_regex`, `_es_nombre_simple`, `_es_pregunta_faq`). Son los
turnos de ~0 ms. La consola debe **decirlo en cada nodo**, o alguien editará un texto esperando
cambiar cómo se entiende "sí" y no pasará nada.

## 3. Decisiones tomadas

| Decisión | Elegido | Motivo |
|---|---|---|
| Alcance de edición | **Solo plantillas.** Los 3 prompts de sistema, en solo lectura | Cubre el 90% de lo que se quiere tocar sin poder degradar exactitud ni latencia |
| Fuente del grafo | **Manifiesto declarado + verificado en runtime**, coloreado con frecuencias reales de `llamada_decisiones` | No se desincroniza del código y además muestra qué pasa de verdad |
| Permisos | **Solo `owner`** edita | Los textos afectan a todos los administradores; se abre más adelante si hace falta |

## 4. Diseño

### 4.1 Manifiesto del grafo (`graph/manifiesto.py`)

Un registro por nodo, al lado del código, que describe:

```python
@dataclass(frozen=True)
class NodoManifiesto:
    id: str
    titulo: str                     # "Identificar finca"
    resumen: str                    # una o dos frases, en español
    tipo: Literal["decision", "pregunta", "efecto", "terminal"]
    usa_llm: bool                   # si puede provocar una llamada al modelo
    caminos_deterministas: tuple[str, ...]   # "sí/no", "3º B", "nombre simple", "FAQ"
    efectos: tuple[str, ...]        # "crea incidencia", "avisa al coordinador"
    destinos: tuple[Arista, ...]    # (destino, condición en lenguaje llano)
    plantillas: tuple[str, ...]     # ids de las plantillas que puede emitir
```

**Verificación en ejecución.** `_avanzar` y `_preguntar` comprueban que el destino está
declarado para el nodo de origen (`estado["nodo_actual"]`):

- en tests y con `BC_GRAFO_ESTRICTO=1`: lanza excepción;
- en producción: registra la violación en la traza y en `/api/metricas`, **y deja continuar la
  llamada**. Una arista mal declarada nunca debe colgarle el teléfono a un vecino.

Así el manifiesto deja de ser documentación decorativa: si alguien añade una transición y no la
declara, la suite falla.

**Aristas reales.** `llamada_decisiones` ya guarda `(llamada_id, turno, nodo)`. Ordenando por
turno dentro de cada llamada salen las transiciones observadas y su frecuencia. Se sirven junto
al manifiesto para colorear el grafo: aristas nunca recorridas en gris, gruesas las frecuentes.

### 4.2 Catálogo de plantillas (`graph/plantillas.py`)

Se extraen los textos hoy incrustados en f-strings a un registro:

```python
@dataclass(frozen=True)
class Plantilla:
    id: str            # "identificar_finca.candidata"
    nodo: str
    descripcion: str   # cuándo se dice
    texto: str         # "He identificado {direccion}. ¿Es correcto?"
    variables: tuple[str, ...]
    pregunta: bool     # debe terminar preguntando
```

Los nodos pasan de interpolar a renderizar:

```python
render(estado, "identificar_finca.candidata", direccion=finca.direccion)
```

`render` resuelve el texto publicado en BD si existe y, si no, el de código. El catálogo se
cachea en memoria y se invalida al guardar: **ninguna llamada toca la BD por turno para
renderizar**, o se pierde el presupuesto de latencia.

**Validación al guardar** (todas obligatorias, el guardado falla si alguna no pasa):

1. Las variables del texto son **exactamente** las declaradas. Una de menos pierde un hecho; una
   de más revienta con `KeyError` durante una llamada real.
2. Si `pregunta=True`, el texto contiene `?`. `conserva_hechos()` exige que la redacción termine
   preguntando; sin interrogante, la paráfrasis se descarta siempre.
3. Sin saltos de línea y con un máximo de caracteres: esto se dice por teléfono, y la redacción
   compara longitudes (`len(texto) > 2.5 * len(plantilla) + 80`).
4. Se ejecuta `hechos_obligatorios(texto)` y se muestran al editor los hechos que tendrán que
   sobrevivir a la paráfrasis. Si salen hechos absurdos, el texto está mal escrito.

**Aviso en la UI, no escondido:** `ultima_pregunta` alimenta el prompt de interpretación del
turno siguiente. Una plantilla ambigua degrada la comprensión, no solo el tono.

**Versionado y auditoría.** Hoy `senales_confianza` registra `version_prompt` por llamada. Si los
textos pasan a ser editables sin versionar, **la auditoría empieza a mentir**.

Resuelto sin cablear el id por cada firma: `render` devuelve un `TextoRenderizado`, subclase de
`str` que lleva encima `plantilla_id` y `version`. Es un `str` normal a todos los efectos, así
que se pasa donde antes iba el literal; `_preguntar` y `_avanzar` lo detectan con un `getattr` y
dejan en la traza `Traza(nodo, "plantilla", "<id> v<N>")`, que acaba en `llamada_decisiones`.
Las plantillas sin sobrescribir se anotan con versión `0`.

Toda edición queda en `auditoria_eventos` con autor, texto anterior y texto nuevo, y existe
"volver al texto original", que borra la sobrescritura.

### 4.3 Catálogos: fincas y profesionales

Ya existen `administradores`, `fincas`, `viviendas` y `especialistas`, y un `GET /api/fincas`.
Falta la pantalla y el resto del CRUD. Se muestra además, por finca, la
`ConfiguracionEfectiva` resuelta (las tres capas: defaults → administrador → finca) en **solo
lectura**: es la forma más rápida de entender por qué una finca se comporta distinto.

**Protección de datos.** `viviendas`, `llamadas`, `incidencias` y las grabaciones son datos
personales de vecinos. En esta entrega: fincas, administradores y especialistas son consultables
y editables por `owner`; las viviendas se listan (son estructura del edificio, no personas); los
datos de vecinos siguen donde están, en Operaciones, con su auditoría. **Pendiente** de criterio
legal antes de ampliar.

### 4.4 API

```
GET    /api/grafo                    # manifiesto + aristas reales (?desde=&hasta=)
GET    /api/prompts                  # los 3 prompts de sistema, SOLO LECTURA, con sus contratos
GET    /api/plantillas               # catálogo con texto efectivo y si está sobrescrito
PUT    /api/plantillas/{id}          # owner: valida, publica, audita
DELETE /api/plantillas/{id}          # owner: vuelve al texto de código
GET    /api/administradores          # + POST/PATCH/DELETE (owner)
GET    /api/fincas/{id}              # detalle + configuración efectiva resuelta
GET    /api/fincas/{id}/viviendas
GET    /api/especialistas            # + POST/PATCH/DELETE (owner)
```

Lectura: cualquier usuario autenticado. Escritura: `owner`, igual que `/api/usuarios`.

### 4.5 Frontend

Dos pantallas nuevas en el `Segmented` de `App.tsx` (hoy Operaciones / Latencia / Usuarios):

- **Flujo** — el grafo. Los nodos se colocan por capas calculadas desde el manifiesto (rango =
  distancia a `siguiente_paso`); el flujo es casi una espina con ramas de reintento, así que un
  **SVG propio basta y no añade dependencia**. Si la interacción crece (arrastrar, minimapa), la
  alternativa es `@xyflow/react`. Al pulsar un nodo: resumen, tipo, si usa LLM, caminos
  deterministas, efectos, destinos con su condición, y sus plantillas editables en línea.
- **Catálogos** — administradores, fincas (con viviendas y configuración efectiva) y
  profesionales.

## 5. Modelo de datos

Una migración Alembic:

```
plantillas            id (PK, = id del catálogo), texto, version (int), actualizada_en, actualizada_por (FK usuarios)
plantilla_versiones   id, plantilla_id (FK), version, texto, autor, creada_en
```

Solo se guardan las plantillas **sobrescritas**. Una tabla vacía significa "todo como en código",
que es el estado inicial.

## 6. Qué NO entra

- Editar los tres prompts de sistema (§2.3). Se ven, no se tocan.
- Editar lógica, umbrales, reintentos o normalización.
- Editar reglas de negocio y efectos: eso es `ConfiguracionParcial` por capas y merece su propia
  entrega.
- Langfuse. Encaja con esto y está en el stack previsto, pero una tabla propia resuelve el
  versionado sin dependencia externa; migrar después es barato.
- Crear grafos nuevos desde la web. `REGISTRO` sigue siendo código.

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Una plantilla editada rompe la paráfrasis y todo cae a texto plano | La métrica `redaccion` ya lo distingue; se muestra en la pantalla de Latencia |
| Caché de plantillas obsoleta con varios procesos | Hoy es un proceso; la caché se invalida por `version`. Con varios trabajadores, revalidar por revisión antes de servir |
| El manifiesto se desincroniza | Verificación en ejecución (§4.1) + test que exige cobertura de todos los nodos |
| Textos peores por edición sin medir | La edición de plantillas no puede alterar la exactitud de interpretación; aun así, medir `respuesta_ms` antes y después según CLAUDE.md |

## 8. Verificación (TDD)

1. Todo nodo de `_ORDEN_ESTANDAR` está en el manifiesto, y al revés.
2. La suite completa (incluidos `gold_interpretacion.jsonl` y `e2e_voz`) no produce **ninguna**
   transición fuera del manifiesto con `BC_GRAFO_ESTRICTO=1`.
3. Toda plantilla del catálogo se emite al menos una vez en la suite; ningún nodo conserva texto
   de cara al vecino incrustado.
4. La validación rechaza: variable ausente, variable sobrante, pregunta sin `?`, salto de línea,
   exceso de longitud.
5. Sobrescribir una plantilla cambia el mensaje del bot de extremo a extremo, y `DELETE`
   restaura el de código.
6. `llamada_decisiones` registra `plantilla_id` y `version` de cada mensaje emitido.
7. Un usuario que no es `owner` recibe `403` en todo `PUT`/`DELETE`; queda en auditoría.
8. Renderizar no ejecuta consultas a BD (caché), y `respuesta_ms` no empeora: `comparar_modelos.py`
   antes y después.


---

## 9. Lo que se construyó (2026-09-13)

Implementado y verificado de extremo a extremo contra PostgreSQL real y a través del túnel.

### Backend

| Pieza | Dónde |
|---|---|
| Manifiesto de los 18 nodos, con etapa, tipo, si usa modelo, caminos deterministas, efectos y destinos con su condición | `graph/manifiesto.py` |
| Verificación de transiciones en ejecución (`_verificar`), estricta bajo pytest y anotada en producción | `graph/nodos.py` |
| Catálogo de 40 plantillas, `render` sobre diccionario en memoria, validación y versionado | `graph/plantillas.py` |
| API: `/api/grafo`, `/api/prompts`, `/api/plantillas` (GET/PUT/DELETE/validar), `/api/catalogos/*` | `flujo_api.py` |
| Tablas `plantillas` y `plantilla_versiones` | migración `0004` |

### Frontend

Dos pantallas nuevas en el `Segmented` del workbench:

- **Flujo**: el grafo en SVG propio (sin dependencia nueva), una fila por etapa, aristas con
  grosor proporcional a las veces recorridas y en gris discontinuo las nunca usadas. Al pulsar un
  bloque: resumen, qué resuelve sin modelo, efectos, a dónde puede ir y por qué, y sus textos
  editables en línea. Debajo, los tres prompts de sistema desplegables con su contrato.
- **Catálogos**: fincas (con viviendas y la configuración efectiva ya resuelta), profesionales y
  administradores.

### Decisiones que cambiaron al implementar

1. **El interrogante puede venir en una variable.** `global.fuera_ambito` termina en `{pregunta}`,
   que trae la pregunta anterior. La regla "debe contener `?`" era falsa, así que se modela con
   `pregunta_en`, el nombre de la variable que lo aporta. Lo detectó un test, no una revisión.
2. **`confirmar_resumen.repetir_solicitud` no es una pregunta**, es un imperativo
   ("Cuénteme de nuevo qué ocurre"). Se declaró sin `pregunta`.
3. **La etapa vive en el manifiesto, no en el frontend.** Es una pista de disposición, pero si
   miente el grafo se lee al revés de como ocurre, así que se declara junto al flujo y hay un
   test que fija el orden.

### Verificación

- 167 tests en verde, ruff limpio. La suite completa recorre el flujo con la verificación
  estricta activa: **ninguna transición fuera del manifiesto**.
- Ciclo real contra PostgreSQL: publicar rechaza el texto que pierde una variable, publicar
  válido persiste y queda en `auditoria_eventos`, restaurar vuelve al código y el historial
  sobrevive.
- La traza de una llamada nueva registra `identificar_finca.saludo v0`.

### Pendiente

- CRUD de escritura de catálogos (hoy son de lectura; los datos se siembran).
- Criterio legal sobre qué datos de vecinos se exponen, antes de ampliar los catálogos.
- Medir `respuesta_ms` antes/después con `comparar_modelos.py` en una máquina sin Ollama ocupado.
