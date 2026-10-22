"""Estado del agente de voz entre turnos. Sin LiveKit: se prueba solo.

Turnos (medido 2026-10-22 con el banco de voz sintética): AssemblyAI cierra el turno en una
pausa a media frase ("Sí. / Eso es."), así que llegan dos finales seguidos, y LiveKit cancela la
respuesta del primero al llegar el segundo. Sin control, la API recibía turnos a la vez, la
respuesta que nunca sonó contaba como oída y un "eso es" confirmaba un resumen que el vecino no
había escuchado. Por eso aquí:

- los turnos van a la API de uno en uno y en orden, y la petición no se corta aunque LiveKit
  cancele la generación (el grafo ya la está procesando);
- antes de mandar un turno se declara `no_reproducida` la respuesta anterior si LiveKit no
  informó de que sonara (no lo hace cuando se corta antes del primer audio);
- lo que el vecino dijo y aún no se mandó va junto en un solo turno;
- si al cerrar el turno el vecino ya estaba hablando otra vez (la pausa era a media frase), o
  el turno solo tiene muletillas ("Ah, vale, perdón."), no se manda nada hasta ver si sigue: si
  llega su siguiente turno, LiveKit cancela la espera y todo va junto. Un turno con contenido
  cerrado en silencio no espera nada.

Al final no hay turno siguiente: si el vecino cortó el cierre, el agente repite lo que no oyó
del aviso (el siguiente paso) antes de colgar. Confirmado por Andreu el 2026-10-21: el siguiente
paso es importante.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)

Accion = Literal["seguir", "colgar", "repetir_y_colgar"]

#: Lo que se espera a que el agente informe de lo que sonó antes de mandar el turno siguiente
ESPERA_INFORME_S = 2.0
#: Tope de la espera a que el vecino calle (ruido de fondo que el VAD toma por voz)
ESPERA_VECINO_S = 4.0
#: Margen para que el vecino siga tras un turno que solo tiene muletillas ("Ah, vale, perdón.")
ESPERA_MULETILLA_S = 0.8
#: Silencio que se exige al vecino antes de mandar un turno retenido (el fin de turno largo de
#: una frase a medias). Medido 2026-10-22: el trozo siguiente de una frase partida llega 1,0-1,4 s
#: después del anterior y el VAD da la voz por acabada en cada coma, así que no basta con mirar
#: si habla en un instante
ESPERA_FINAL_S = 1.2

#: Palabras que solas no dicen nada al grafo: tras ellas el vecino suele seguir. Sin "sí" ni "no"
#: (son respuestas) y "vale" o "bueno" sueltos tampoco (valen por un "sí")
_MULETILLAS = frozenset(
    "ah eh oh ay uy pues mire mira oiga oye perdon perdone perdona disculpe hola buenas "
    "bueno vale a ver entonces este esto y o sea".split()
)
_RESPUESTAS_SUELTAS = frozenset({"vale", "bueno"})


def _palabras(texto: str) -> list[str]:
    sin_acentos = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", sin_acentos)


def solo_muletillas(texto: str) -> bool:
    """El turno no trae nada que el grafo pueda usar: el vecino aún no ha dicho lo que quiere."""
    palabras = _palabras(texto)
    if not palabras or (len(palabras) == 1 and palabras[0] in _RESPUESTAS_SUELTAS):
        return False
    return all(p in _MULETILLAS for p in palabras)


class ApiTurnos(Protocol):
    async def turno(self, llamada_id: str, texto: str) -> dict[str, Any]: ...

    async def entrega(
        self, llamada_id: str, turno: int, interrumpida: bool, texto_entregado: str, ratio: float
    ) -> dict[str, Any]: ...


class ControlLlamada:
    def __init__(self, llamada_id: str) -> None:
        self.llamada_id = llamada_id
        self.turno = 0
        self.finalizada = False
        self.texto = ""
        # La respuesta del turno actual aún no consta como sonada (ni como cortada)
        self.pendiente_de_informe = False
        self._enviados: set[str] = set()
        self._en_curso: asyncio.Future[dict[str, Any]] | None = None
        self._informes: set[asyncio.Future[Any]] = set()
        self._cierre_con_voz = False
        self._por_enviar: list[str] = []

    def nuevo_turno(self, vista: dict[str, Any]) -> str:
        """Guarda el turno que va a sonar y devuelve su texto."""
        self.turno = int(vista.get("turno", 0))
        self.finalizada = bool(vista.get("finalizada"))
        self.texto = " ".join(vista.get("mensajes_bot") or []).strip()
        self.pendiente_de_informe = bool(self.texto)
        return self.texto

    def por_enviar(self, mensajes: Iterable[Any]) -> str:
        """Lo que dijo el vecino (mensajes `user` del contexto de LiveKit) que la API aún no ha
        recibido, junto. Cuenta como enviado al llamar a `enviar_turno`: si LiveKit cancela la
        generación antes, sigue pendiente para el turno siguiente."""
        nuevos = [
            m
            for m in mensajes
            if getattr(m, "role", None) == "user"
            and (getattr(m, "text_content", None) or "").strip()
            and m.id not in self._enviados
        ]
        self._por_enviar = [m.id for m in nuevos]
        return " ".join(m.text_content.strip() for m in nuevos)

    def al_cerrar_turno(self, vecino_hablando: bool) -> None:
        """AssemblyAI ha cerrado un turno; `vecino_hablando`: el VAD oye voz en ese momento."""
        self._cierre_con_voz = vecino_hablando

    async def esperar_antes_de_enviar(
        self,
        texto: str,
        hablando: Callable[[], bool],
        espera_muletilla_s: float = ESPERA_MULETILLA_S,
        espera_final_s: float = ESPERA_FINAL_S,
        maximo_s: float = ESPERA_VECINO_S,
        paso_s: float = 0.02,
    ) -> bool:
        """Antes de mandar `texto`, espera si el vecino no ha terminado de hablar:

        - el turno se cerró con el vecino hablando, o solo tiene muletillas y en un margen corto
          vuelve a hablar: hasta que lleve `espera_final_s` callado.

        Si mientras tanto llega su turno siguiente, LiveKit cancela esta espera y el texto va
        junto con el siguiente. Con tope, por si el VAD toma ruido por voz. Devuelve si ha
        esperado. El cierre se consume: solo cuenta para su turno."""
        con_voz, self._cierre_con_voz = self._cierre_con_voz, False
        reloj = asyncio.get_running_loop().time
        if not con_voz:
            if not solo_muletillas(texto):
                return False
            limite = reloj() + espera_muletilla_s
            while not hablando():
                if reloj() >= limite:
                    return True
                await asyncio.sleep(paso_s)
        # Se cuenta desde ya como si hablara: tras cerrar el turno LiveKit le da por callado
        # aunque siga, y el VAD tarda en volver a oírle
        ultima_voz, maximo = reloj(), reloj() + maximo_s
        while reloj() < maximo and reloj() - ultima_voz < espera_final_s:
            if hablando():
                ultima_voz = reloj()
            await asyncio.sleep(paso_s)
        return True

    def informe(self) -> tuple[int, str]:
        """LiveKit avisa de que la respuesta ha sonado (entera o cortada): el turno y el texto a
        los que se refiere, tomados en ese momento y no cuando se manda el informe."""
        self.pendiente_de_informe = False
        return self.turno, self.texto

    def seguir_informe(self, tarea: asyncio.Future[Any]) -> None:
        """El turno siguiente espera a que este informe llegue a la API."""
        self._informes.add(tarea)
        tarea.add_done_callback(self._informes.discard)

    async def enviar_turno(self, api: ApiTurnos, texto: str) -> dict[str, Any]:
        """Manda el turno del vecino detrás del anterior. Si quien espera se cancela, la petición
        sigue y su resultado queda guardado (y pendiente de informe, porque no sonará)."""
        anterior = self._en_curso
        self._enviados.update(self._por_enviar)
        self._por_enviar = []

        async def hacer() -> dict[str, Any]:
            if anterior is not None:
                await asyncio.wait({anterior})
            if self._informes:
                await asyncio.wait(set(self._informes), timeout=ESPERA_INFORME_S)
            if self.pendiente_de_informe:
                self.pendiente_de_informe = False
                try:
                    await api.entrega(self.llamada_id, self.turno, True, "", 0.0)
                except Exception:  # noqa: BLE001 - sin el informe el turno sigue adelante
                    logger.warning(
                        "No se pudo declarar como no reproducido el turno %s", self.turno
                    )
            vista = await api.turno(self.llamada_id, texto)
            self.nuevo_turno(vista)
            return vista

        self._en_curso = asyncio.ensure_future(hacer())
        return await asyncio.shield(self._en_curso)

    def al_terminar_de_hablar(self, entrega: dict[str, Any]) -> tuple[Accion, list[str]]:
        """`entrega`: la `entrega_ultima` que devuelve la API tras registrar lo que sonó."""
        if not self.finalizada:
            return "seguir", []
        cortados = [
            p["texto"]
            for p in entrega.get("piezas") or []
            if p["importancia"] == "aviso" and p["estado"] in ("no_oida", "no_entregada")
        ]
        return ("repetir_y_colgar", cortados) if cortados else ("colgar", [])
