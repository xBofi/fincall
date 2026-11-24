/**
 * Todos los textos de la vista de enrutado, con claves estables (`routing.*`). Pensado para
 * pasar tal cual al catálogo i18n de la consola: las claves no cambian, solo de dónde salen.
 * El idioma sale de `<html lang>`; la dirección (RTL para ár/ur), de `<html dir>`.
 */

type Messages = Record<string, string>

const es: Messages = {
  'routing.eyebrow': 'LangGraph · grafo {graph} · {nodes} nodos · {edges} rutas',
  'routing.title': 'Cómo se enruta una llamada',
  'routing.mode.structure': 'Estructura',
  'routing.mode.live': 'En directo',
  'routing.mode.replay': 'Repetir llamada',
  'routing.mode.demo': 'Demo',
  'routing.mode.label': 'Qué mostrar',
  'routing.reload': 'Volver a leer el grafo',
  'routing.loading': 'Leyendo el grafo compilado…',
  'routing.error': 'No se pudo leer el grafo: {error}',
  'routing.retry': 'Reintentar',
  'routing.drift.title': 'El manifiesto y el grafo compilado no coinciden',
  'routing.violations.title': 'Transiciones observadas que el manifiesto no declara',
  'routing.source': 'Nodos leídos del grafo compilado ({compiled} aristas condicionales); cada ruta, con la condición que declara el manifiesto y verificada contra él.',

  'routing.role.entry': 'Entrada',
  'routing.role.router': 'Enrutador',
  'routing.role.llm': 'LLM',
  'routing.role.tool': 'Herramienta',
  'routing.role.human': 'Derivar a humano',
  'routing.role.end': 'Fin',
  'routing.legend': 'Leyenda',
  'routing.legend.retry': 'Puede repreguntar',
  'routing.legend.taken': 'Ruta tomada',
  'routing.legend.current': 'Salto actual',
  'routing.legend.width': 'Grosor = veces recorrida',
  'routing.legend.hidden': 'Las vueltas al reparto y las salidas globales se muestran al seleccionar o al recorrerse',

  'routing.node.visits': '{n}×',
  'routing.node.ms': '{ms} ms',
  'routing.node.running': 'ejecutando',
  'routing.node.waiting': 'esperando al vecino',
  'routing.node.aria': '{title}, {role}',
  'routing.entry.edge': 'retoma en el nodo guardado ({router})',

  'routing.live.empty.title': 'No hay ninguna llamada en curso',
  'routing.live.empty.body': 'Empieza una en «Simular llamada» y vuelve aquí, o mira la demo grabada.',
  'routing.live.connected': 'En directo',
  'routing.live.reconnecting': 'Reconectando…',
  'routing.live.ended': 'Llamada terminada',
  'routing.live.follow': 'Seguir en directo',

  'routing.replay.pick': 'Elige una llamada',
  'routing.replay.empty': 'Aún no hay llamadas guardadas.',
  'routing.replay.legacy': 'Llamada anterior a la traza por nodo: secuencia reconstruida de las decisiones, sin tiempos ni motivos.',
  'routing.replay.loading': 'Cargando la llamada…',
  'routing.replay.active': 'en curso',

  'routing.demo.note': 'Llamada guionizada grabada a través del grafo real ({llm}, {date}). Sin telefonía: se reproduce en el navegador.',
  'routing.demo.speed': 'Velocidad',

  'routing.timeline.label': 'Línea de tiempo de la llamada',
  'routing.timeline.play': 'Reproducir',
  'routing.timeline.pause': 'Pausa',
  'routing.timeline.prev': 'Paso anterior',
  'routing.timeline.next': 'Paso siguiente',
  'routing.timeline.first': 'Al principio',
  'routing.timeline.last': 'Al final',
  'routing.timeline.step': 'Paso {i} de {n}',
  'routing.timeline.turn': 'Turno {turn}',
  'routing.timeline.keys': '← → pasos · Inicio/Fin · Espacio reproduce',
  'routing.timeline.empty': 'Sin pasos todavía',

  'routing.panel.decision': 'Decisión',
  'routing.panel.state': 'Estado',
  'routing.panel.conversation': 'Conversación',
  'routing.panel.node': 'Nodo',
  'routing.panel.pick': 'Avanza la línea de tiempo o elige un nodo.',

  'routing.decision.turn': 'Turno {turn} · paso {i}',
  'routing.decision.caller': 'El vecino dijo',
  'routing.decision.greeting': 'Inicio de la llamada: aún no ha hablado nadie',
  'routing.decision.route': 'Ruta',
  'routing.decision.condition': 'Condición que se cumplió',
  'routing.decision.declared': 'declarada en el manifiesto',
  'routing.decision.undeclared': 'Arista no declarada en el manifiesto',
  'routing.decision.waits': 'Pregunta y espera la respuesta del vecino: el grafo se detiene hasta el siguiente turno.',
  'routing.decision.ends': 'La llamada termina aquí.',
  'routing.decision.latency': 'Nodo',
  'routing.decision.llmCalls': 'Llamadas LLM',
  'routing.decision.llmMs': 'Tiempo LLM',
  'routing.decision.origin': 'Interpretación',
  'routing.decision.origin.deterministic': 'determinista, sin modelo',
  'routing.decision.origin.llm': 'modelo',
  'routing.decision.origin.llm_speculative': 'modelo, adelantada sobre el parcial',
  'routing.decision.intent': 'Intención detectada',
  'routing.decision.confidence': 'Confianza',
  'routing.decision.confidence.none': 'El modelo no da una puntuación: estas son las señales que el código comprueba.',
  'routing.decision.tokens': 'Tokens / coste',
  'routing.decision.tokens.none': 'no medido',
  'routing.decision.tokens.why': 'El proveedor no devuelve tokens en este flujo: no se muestran cifras inventadas.',
  'routing.decision.extracted': 'Lo que entendió',
  'routing.decision.rationale': 'Razonamiento del nodo',
  'routing.decision.validations': 'Validaciones',
  'routing.decision.said': 'El asistente dijo',
  'routing.decision.synthetic': 'Paso reconstruido: sin tiempos ni condición.',
  'routing.decision.running': 'Ejecutando {node}…',

  'routing.state.changed': 'Cambios en este paso',
  'routing.state.none': 'Este paso no cambió datos de la llamada.',
  'routing.state.key': 'Campo',
  'routing.state.before': 'Antes',
  'routing.state.after': 'Después',
  'routing.state.all': 'Estado acumulado',

  'routing.conversation.caller': 'Vecino',
  'routing.conversation.bot': 'Asistente',
  'routing.conversation.jump': 'Ir al turno {turn} en el grafo',
  'routing.conversation.empty': 'Sin conversación todavía.',

  'routing.node.summary': 'Qué hace',
  'routing.node.exits': 'A dónde puede ir',
  'routing.node.deterministic': 'Se resuelve sin modelo',
  'routing.node.effects': 'Efectos',
  'routing.node.end': 'Fin',

  'routing.minimap': 'Minimapa del grafo',
  'routing.canvas': 'Grafo de enrutado de la llamada',
}

const en: Messages = {
  'routing.eyebrow': 'LangGraph · graph {graph} · {nodes} nodes · {edges} routes',
  'routing.title': 'How a call is routed',
  'routing.mode.structure': 'Structure',
  'routing.mode.live': 'Live',
  'routing.mode.replay': 'Replay a call',
  'routing.mode.demo': 'Demo',
  'routing.mode.label': 'What to show',
  'routing.reload': 'Reload the graph',
  'routing.loading': 'Reading the compiled graph…',
  'routing.error': 'Could not read the graph: {error}',
  'routing.retry': 'Retry',
  'routing.drift.title': 'The manifest and the compiled graph disagree',
  'routing.violations.title': 'Observed transitions the manifest does not declare',
  'routing.source': 'Nodes read from the compiled graph ({compiled} conditional edges); every route carries the condition the manifest declares and is checked against it.',

  'routing.role.entry': 'Entry',
  'routing.role.router': 'Router',
  'routing.role.llm': 'LLM',
  'routing.role.tool': 'Tool',
  'routing.role.human': 'Human handoff',
  'routing.role.end': 'End',
  'routing.legend': 'Legend',
  'routing.legend.retry': 'May re-ask',
  'routing.legend.taken': 'Route taken',
  'routing.legend.current': 'Current hop',
  'routing.legend.width': 'Width = times taken',
  'routing.legend.hidden': 'Returns to the dispatcher and global exits show when selected or taken',

  'routing.node.visits': '{n}×',
  'routing.node.ms': '{ms} ms',
  'routing.node.running': 'running',
  'routing.node.waiting': 'waiting for the caller',
  'routing.node.aria': '{title}, {role}',
  'routing.entry.edge': 'resumes at the saved node ({router})',

  'routing.live.empty.title': 'No call in progress',
  'routing.live.empty.body': 'Start one in “Simulate call” and come back, or watch the recorded demo.',
  'routing.live.connected': 'Live',
  'routing.live.reconnecting': 'Reconnecting…',
  'routing.live.ended': 'Call ended',
  'routing.live.follow': 'Follow live',

  'routing.replay.pick': 'Pick a call',
  'routing.replay.empty': 'No saved calls yet.',
  'routing.replay.legacy': 'Call recorded before per-node tracing: sequence rebuilt from decisions, without timings or reasons.',
  'routing.replay.loading': 'Loading the call…',
  'routing.replay.active': 'in progress',

  'routing.demo.note': 'Scripted call recorded through the real graph ({llm}, {date}). No telephony: it plays in the browser.',
  'routing.demo.speed': 'Speed',

  'routing.timeline.label': 'Call timeline',
  'routing.timeline.play': 'Play',
  'routing.timeline.pause': 'Pause',
  'routing.timeline.prev': 'Previous step',
  'routing.timeline.next': 'Next step',
  'routing.timeline.first': 'To the start',
  'routing.timeline.last': 'To the end',
  'routing.timeline.step': 'Step {i} of {n}',
  'routing.timeline.turn': 'Turn {turn}',
  'routing.timeline.keys': '← → steps · Home/End · Space plays',
  'routing.timeline.empty': 'No steps yet',

  'routing.panel.decision': 'Decision',
  'routing.panel.state': 'State',
  'routing.panel.conversation': 'Conversation',
  'routing.panel.node': 'Node',
  'routing.panel.pick': 'Move the timeline or pick a node.',

  'routing.decision.turn': 'Turn {turn} · step {i}',
  'routing.decision.caller': 'The caller said',
  'routing.decision.greeting': 'Call start: nobody has spoken yet',
  'routing.decision.route': 'Route',
  'routing.decision.condition': 'Condition that fired',
  'routing.decision.declared': 'declared in the manifest',
  'routing.decision.undeclared': 'Edge not declared in the manifest',
  'routing.decision.waits': 'Asks and waits for the caller: the graph stops until the next turn.',
  'routing.decision.ends': 'The call ends here.',
  'routing.decision.latency': 'Node',
  'routing.decision.llmCalls': 'LLM calls',
  'routing.decision.llmMs': 'LLM time',
  'routing.decision.origin': 'Interpretation',
  'routing.decision.origin.deterministic': 'deterministic, no model',
  'routing.decision.origin.llm': 'model',
  'routing.decision.origin.llm_speculative': 'model, started on the partial transcript',
  'routing.decision.intent': 'Detected intent',
  'routing.decision.confidence': 'Confidence',
  'routing.decision.confidence.none': 'The model gives no score: these are the signals the code checks.',
  'routing.decision.tokens': 'Tokens / cost',
  'routing.decision.tokens.none': 'not measured',
  'routing.decision.tokens.why': 'The provider does not return token counts in this flow: no made-up numbers are shown.',
  'routing.decision.extracted': 'What it understood',
  'routing.decision.rationale': 'Node rationale',
  'routing.decision.validations': 'Validations',
  'routing.decision.said': 'The assistant said',
  'routing.decision.synthetic': 'Rebuilt step: no timings or condition.',
  'routing.decision.running': 'Running {node}…',

  'routing.state.changed': 'Changes in this step',
  'routing.state.none': 'This step did not change call data.',
  'routing.state.key': 'Field',
  'routing.state.before': 'Before',
  'routing.state.after': 'After',
  'routing.state.all': 'Accumulated state',

  'routing.conversation.caller': 'Caller',
  'routing.conversation.bot': 'Assistant',
  'routing.conversation.jump': 'Go to turn {turn} in the graph',
  'routing.conversation.empty': 'No conversation yet.',

  'routing.node.summary': 'What it does',
  'routing.node.exits': 'Where it can go',
  'routing.node.deterministic': 'Resolved without a model',
  'routing.node.effects': 'Effects',
  'routing.node.end': 'End',

  'routing.minimap': 'Graph minimap',
  'routing.canvas': 'Call routing graph',
}

const CATALOG: Record<string, Messages> = { es, en }

export function locale(): string {
  const lang = (typeof document !== 'undefined' ? document.documentElement.lang : '') || 'es'
  const short = lang.slice(0, 2).toLowerCase()
  return short in CATALOG ? short : 'es'
}

export function isRtl(): boolean {
  return typeof document !== 'undefined' && document.documentElement.dir === 'rtl'
}

export function t(key: string, vars: Record<string, string | number> = {}): string {
  const text = CATALOG[locale()][key] ?? es[key] ?? key
  return text.replace(/\{(\w+)\}/g, (_, k: string) => (k in vars ? String(vars[k]) : `{${k}}`))
}

/** Para comprobar en tests que ningún idioma se queda sin una clave. */
export const MESSAGE_KEYS = { es: Object.keys(es), en: Object.keys(en) }
