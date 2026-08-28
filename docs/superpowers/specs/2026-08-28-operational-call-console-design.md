# Consola operativa de llamadas — diseño

## Objetivo

Convertir la consola de validación actual en un producto operativo completo. Debe
permitir efectuar llamadas de prueba, auditar su resultado y comprender cada
decisión del asistente desde una interfaz limpia, viva y preparada para uso real.

La fuente de verdad será persistente y segura desde el inicio: no habrá métricas
ni historiales solo en memoria.

## Personas y acceso

La aplicación autentica mediante Google OpenID Connect con Authorization Code y
PKCE. El backend valida la identidad y mantiene una sesión revocable en
PostgreSQL; la cookie de sesión será `HttpOnly`, `Secure` y `SameSite`.

Los roles son:

- `owner`: acceso total, incluido gobierno de usuarios y auditoría.
- `administrador`: acceso operativo, gestión de usuarios autorizados y llamadas.
- `calidad`: acceso de solo lectura a llamadas, transcripciones, trazas y audio.

Toda ruta de API comprueba el rol en el servidor. Las cuentas nuevas quedan
denegadas hasta que un `owner` o `administrador` las autorice mediante lista
blanca. Las acciones sensibles —inicio de sesión, cambio de rol, reproducción o
descarga de audio, y borrado por retención— se registran en una auditoría.

## Persistencia y ciclo de una llamada

PostgreSQL es la fuente de verdad para llamadas, turnos, métricas, decisiones,
usuarios, sesiones y eventos de auditoría. El modelo separa la llamada del
detalle por turno para poder consultar tendencias sin desnormalizar trazas.

Cada llamada guarda:

- Identificador, inicio, fin y estado: activa, finalizada, abandonada o requiere
  revisión.
- Resultado de negocio, finca, vivienda, incidencia, resumen y motivo de
  revisión.
- Transcripción cronológica y una instantánea de la configuración/grafo aplicado.
- Por turno: fin de voz del cliente, VAD/EOU, STT, red, interpretación, grafo,
  inicio de TTS, latencia total de respuesta y calificación.
- Eventos de decisión: nodo de entrada y salida, evidencia disponible, regla o
  modelo que intervino, acción elegida y justificación humana legible.

El audio queda fuera de PostgreSQL en un almacén de objetos privado compatible
con S3. Cada pista tiene metadatos de duración, formato, checksum, hora de
expiración y llamada asociada. Se guardan la pista del cliente y la respuesta
del asistente, sincronizadas con la transcripción; el reproductor las presenta
como una llamada continua. Nunca se publican URL permanentes: la API autoriza
cada escucha y emite una URL firmada de vida muy corta.

La retención es de catorce días. Un trabajo programado elimina los objetos y sus
metadatos de audio al vencer, deja constancia auditable del borrado y no expone
ninguna grabación expirada.

## Servicios y APIs

Se añade una capa de repositorios para llamadas operativas y almacenamiento de
audio, sin acoplar el dominio a un proveedor concreto. El adaptador de objetos
usa configuración S3-compatible; permite un proveedor real en despliegue y un
adaptador local aislado para desarrollo.

Las APIs se organizan por casos de uso:

- Sesión: iniciar/cerrar sesión y obtener el usuario actual.
- Administración: listar usuarios autorizados y actualizar roles, solo para
  `owner` y `administrador`.
- Operaciones: listar llamadas con filtros/paginación, obtener el expediente de
  una llamada y consultar agregados por periodo.
- Audio: registrar pistas durante la llamada y solicitar una reproducción
  autorizada.
- Auditoría: registrar y consultar eventos de acceso según rol.

Las respuestas de operación contienen datos reales; una métrica sin muestra se
representa como ausencia, nunca se inventa un valor para llenar la interfaz.

## Interfaz

La pantalla conserva la capacidad de llamar, pero adopta la estructura de
centro de operaciones elegida:

1. Cabecera de producto con estado de sesión, salud del servicio y acción de
   nueva llamada.
2. Franja de métricas: volumen, duración, respuesta del asistente, porcentaje
   resuelto/no resuelto y cumplimiento de umbral. Todos los valores proceden de
   filtros de periodo activos.
3. Cola izquierda: llamadas activas y recientes con estado, prioridad, resultado
   y señales de revisión; filtros por periodo, estado, resultado y latencia.
4. Expediente central: resumen, reproductor sincronizado, transcripción marcada
   por interlocutor, cronología de latencia, decisiones y flujo de grafo. El
   usuario puede saltar entre audio, texto, evento y decisión sin perder el
   contexto.
5. Panel derecho: llamada en vivo, controles de voz, canal de conversación y
   configuración técnica. Al elegir una llamada histórica se transforma en un
   inspector compacto, sin ocultar el expediente.

La jerarquía visual es moderna y mínima: fondo claro, tinta oscura, un único
acento cromático, tipografía con personalidad y densidad controlada. Las
animaciones se limitan a cambios de estado, entrada de datos y progreso de audio;
usan `transform`/`opacity`, respetan `prefers-reduced-motion` y no distraen de la
operación.

## Sistema visual Hallmark

El rediseño seguirá Hallmark como una página de aplicación, no una landing:

- Género: modern-minimal.
- Macroestructura: Workbench / centro de operaciones.
- Sin enriquecimiento decorativo: la información y la interacción son el foco.
- Tokens OKLCH, escala de espaciado de cuatro puntos y tipografías por rol.
- Componentes con estados accesibles, foco visible, contraste suficiente y
  adaptación comprobada en 320, 375, 414 y 768 píxeles.
- Auditoría Hallmark antes de entrega para evitar patrones visuales genéricos.

## Seguridad y límites

- CORS limitado a orígenes configurados; secretos solo en entorno seguro.
- Validación de cargas de audio, tamaño y tipo MIME; checksum al persistir.
- Limitación de peticiones y control de autorización en servidor.
- No se guardan tokens de Google en el navegador ni se confía en roles enviados
  por el cliente.
- El audio y las transcripciones se consideran datos sensibles: no hay acceso
  público, caché compartida ni enlaces reutilizables.

## Verificación

Las pruebas cubrirán autorización por rol, denegación de usuario no autorizado,
retención de catorce días, seguridad de audio, persistencia de llamadas,
agregados de latencia, filtros/paginación y todas las rutas de la llamada. El
frontend se verificará con pruebas de compilación/lint y recorridos reales de
navegador, incluidos escritorio y los cuatro anchos móviles de Hallmark.
