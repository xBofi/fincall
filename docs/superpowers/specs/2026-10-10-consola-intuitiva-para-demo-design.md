# Consola más intuitiva para la demo a clientes

Fecha: 2026-10-10 · Estado: aprobado · Ámbito: solo `frontend/`

## Problema

La consola se enseñará a clientes la semana del 2026-10-12. Hoy:

- Al simular una llamada no se ven las fincas ni sus datos: están en otra pantalla y el detalle de
  cada finca exige pulsar en una tabla. Quien simula no se sabe de memoria direcciones, viviendas
  ni profesionales, así que no puede seguir la llamada ni comprobar que el bot acierta.
- Vocabulario interno a la vista del cliente: "dossier", "rastro verificable", nodos, grafo, p50,
  Streaming/Whisper, y códigos crudos (`incidencia_creada`, `fontaneria`, ids de vivienda).
- La pantalla principal mezcla llamada en directo e historial; la conversación queda en un lateral.
- Con cero llamadas, el indicador de respuesta dice "por encima del objetivo".

## Diseño

Navegación: **Simular llamada · Historial · Fincas y profesionales · Flujo · Rendimiento ·
Usuarios** (esta última solo owner).

1. **Simular llamada** (pantalla por defecto), tres columnas que hacen scroll por separado:
   - *Conversación*: iniciar/colgar, estado de voz y ajustes de voz en un popover con nombres
     comprensibles. Sin llamada, explica qué hace y qué no hace el asistente (hechos del código:
     manifiesto del grafo y `validar_datos`/`asignar_especialista`), y avisa de que en la demo los
     avisos a profesionales se simulan.
   - *Lo que ha entendido el asistente*: lista de pasos (finca, vivienda, nombre, problema, tipo,
     urgencia, confirmación, incidencia, aviso) con hecho / preguntando ahora / pendiente, y el
     resultado final en una frase. El detalle técnico (traza, interpretación, validaciones) queda
     plegado.
   - *Base de datos*: fincas y profesionales con buscador. Cada finca muestra contacto,
     administrador, viviendas agrupadas por portal, qué profesional atiende cada tipo de avería y
     cómo avisa según la urgencia. La finca, la vivienda y el profesional que el bot identifica se
     resaltan y se despliegan solos.
2. **Historial**: indicadores globales + lista de llamadas y su detalle, con la finca por
   dirección, la vivienda por etiqueta, el resultado en palabras y la conversación como chat. Las
   decisiones internas, plegadas.
3. **Fincas y profesionales**: el mismo componente de base de datos a pantalla completa.
4. **Flujo**: el grafo se dibuja como estrella (eje `siguiente_paso` a la izquierda, pasos en columna, salidas de error al lado, salidas globales agrupadas y solo trazadas al seleccionar, ↻ en vez de lazos). **Rendimiento** (antes Latencia): sin cambios de fondo.

Las etiquetas legibles (categorías, urgencias, resultados, canales) viven en `textos.ts`; un código
sin traducción se muestra capitalizado, nunca se oculta.

## Fuera de alcance

Cambios de backend o de API, edición de catálogos desde la consola.

## Verificación

`tsc -b`, `oxlint`, `vite build`, y capturas con Playwright de cada pantalla a 1440×900 y
1280×800, con una llamada simulada por texto de principio a fin (backend en modo memoria).
