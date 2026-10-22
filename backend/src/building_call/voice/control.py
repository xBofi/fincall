"""Estado del agente de voz entre turnos. Sin LiveKit: se prueba solo.

Turnos (medido 2026-10-22 con el banco de voz sintética): AssemblyAI cierra el turno en una
pausa a media frase ("Sí. / Eso es."), así que llegan dos finales seguidos, y LiveKit cancela la
respuesta del primero al llegar el segundo. Sin control, la API recibía turnos a la vez, la
respuesta que nunca sonó contaba como oída y un "eso es" confirmaba un resumen que el vecino no
había escuchado. Por eso aquí:

- los turnos van a la API de uno en uno y en orden, y la petición no se corta aunque LiveKit
  cancele la generación (el grafo ya la está procesando);
- antes de mandar un turno se declara `not_played` la respuesta anterior si LiveKit no
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

Action = Literal["proceed", "hang_up", "repeat_and_hang_up"]

#: Lo que se espera a que el agente informe de lo que sonó antes de mandar el turno siguiente
REPORT_WAIT_S = 2.0
#: Tope de la espera a que el vecino calle (ruido de fondo que el VAD toma por voz)
CALLER_WAIT_S = 4.0
#: Margen para que el vecino siga tras un turno que solo tiene muletillas ("Ah, vale, perdón.")
FILLER_WAIT_S = 0.8
#: Silencio que se exige al vecino antes de mandar un turno retenido (el fin de turno largo de
#: una frase a medias). Medido 2026-10-22: el trozo siguiente de una frase partida llega 1,0-1,4 s
#: después del anterior y el VAD da la voz por acabada en cada coma, así que no basta con mirar
#: si habla en un instante
FINAL_WAIT_S = 1.2

#: Palabras que solas no dicen nada al grafo: tras ellas el vecino suele seguir. Sin "sí" ni "no"
#: (son respuestas) y "vale" o "bueno" sin nada más tampoco (valen por un "sí"). "a ver" y
#: "o sea" van juntas: "A." u "O." sueltas son la letra de una puerta
_VERBAL_TICS = frozenset(
    "ah eh oh ay uy pues mire mira oiga oye perdon perdone perdona disculpe hola buenas "
    "bueno vale a_ver entonces este esto y o_sea".split()
)
_LOOSE_ANSWERS = frozenset({"vale", "bueno"})


def _words(text: str) -> list[str]:
    without_accents = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    words = " ".join(re.findall(r"[a-z0-9]+", without_accents))
    return re.sub(r"\b(a) (ver)\b|\b(o) (sea)\b", r"\1\3_\2\4", words).split()


def only_fillers(text: str) -> bool:
    """El turno no trae nada que el grafo pueda usar: el vecino aún no ha dicho lo que quiere."""
    words = _words(text)
    if not words or all(p in _LOOSE_ANSWERS for p in words):
        return False
    return all(p in _VERBAL_TICS for p in words)


class TurnsApi(Protocol):
    async def turn(self, call_id: str, text: str) -> dict[str, Any]: ...

    async def delivery(
        self, call_id: str, turn: int, interrupted: bool, delivered_text: str, ratio: float
    ) -> dict[str, Any]: ...


class CallControl:
    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.turn = 0
        self.finished = False
        self.text = ""
        # La respuesta del turno actual aún no consta como sonada (ni como cortada)
        self.pending_report = False
        self._sent: set[str] = set()
        self._in_progress: asyncio.Future[dict[str, Any]] | None = None
        self._reports: set[asyncio.Future[Any]] = set()
        self._close_with_voice = False
        self._to_send: list[str] = []

    def new_turn(self, view: dict[str, Any]) -> str:
        """Guarda el turno que va a sonar y devuelve su texto."""
        self.turn = int(view.get("turn", 0))
        self.finished = bool(view.get("finished"))
        self.text = " ".join(view.get("bot_messages") or []).strip()
        self.pending_report = bool(self.text)
        return self.text

    def to_send(self, messages: Iterable[Any]) -> str:
        """Lo que dijo el vecino (mensajes `user` del contexto de LiveKit) que la API aún no ha
        recibido, junto. Cuenta como enviado al llamar a `send_turn`: si LiveKit cancela la
        generación antes, sigue pendiente para el turno siguiente."""
        new_items = [
            m
            for m in messages
            if getattr(m, "role", None) == "user"
            and (getattr(m, "text_content", None) or "").strip()
            and m.id not in self._sent
        ]
        self._to_send = [m.id for m in new_items]
        return " ".join(m.text_content.strip() for m in new_items)

    def on_turn_closed(self, caller_speaking: bool) -> None:
        """AssemblyAI ha cerrado un turno; `caller_speaking`: el VAD oye voz en ese momento."""
        self._close_with_voice = caller_speaking

    async def wait_before_sending(
        self,
        text: str,
        speaking: Callable[[], bool],
        filler_wait_s: float = FILLER_WAIT_S,
        final_wait_s: float = FINAL_WAIT_S,
        max_s: float = CALLER_WAIT_S,
        step_s: float = 0.02,
    ) -> bool:
        """Antes de mandar `text`, espera si el vecino no ha terminado de hablar:

        - el turno se cerró con el vecino hablando, o solo tiene muletillas y en un margen corto
          vuelve a hablar: hasta que lleve `final_wait_s` callado.

        Si mientras tanto llega su turno siguiente, LiveKit cancela esta espera y el texto va
        junto con el siguiente. Con tope, por si el VAD toma ruido por voz. Devuelve si ha
        esperado. El cierre se consume: solo cuenta para su turno."""
        with_voice, self._close_with_voice = self._close_with_voice, False
        clock = asyncio.get_running_loop().time
        if not with_voice:
            if not only_fillers(text):
                return False
            limit = clock() + filler_wait_s
            while not speaking():
                if clock() >= limit:
                    return True
                await asyncio.sleep(step_s)
        # Se cuenta desde ya como si hablara: tras cerrar el turno LiveKit le da por callado
        # aunque siga, y el VAD tarda en volver a oírle
        last_voice, deadline = clock(), clock() + max_s
        while clock() < deadline and clock() - last_voice < final_wait_s:
            if speaking():
                last_voice = clock()
            await asyncio.sleep(step_s)
        return True

    def report(self) -> tuple[int, str]:
        """LiveKit avisa de que la respuesta ha sonado (entera o cortada): el turno y el texto a
        los que se refiere, tomados en ese momento y no cuando se manda el informe."""
        self.pending_report = False
        return self.turn, self.text

    def track_report(self, task: asyncio.Future[Any]) -> None:
        """El turno siguiente espera a que este informe llegue a la API."""
        self._reports.add(task)
        task.add_done_callback(self._reports.discard)

    async def send_turn(self, api: TurnsApi, text: str) -> dict[str, Any]:
        """Manda el turno del vecino detrás del anterior. Si quien espera se cancela, la petición
        sigue y su resultado queda guardado (y pendiente de informe, porque no sonará)."""
        previous = self._in_progress
        self._sent.update(self._to_send)
        self._to_send = []

        async def hacer() -> dict[str, Any]:
            if previous is not None:
                await asyncio.wait({previous})
            if self._reports:
                await asyncio.wait(set(self._reports), timeout=REPORT_WAIT_S)
            if self.pending_report:
                self.pending_report = False
                try:
                    await api.delivery(self.call_id, self.turn, True, "", 0.0)
                except Exception:  # noqa: BLE001 - sin el informe el turno sigue adelante
                    logger.warning(
                        "No se pudo declarar como no reproducido el turno %s", self.turn
                    )
            view = await api.turn(self.call_id, text)
            self.new_turn(view)
            return view

        in_progress = self._in_progress = asyncio.ensure_future(hacer())
        try:
            return await asyncio.shield(in_progress)
        except asyncio.CancelledError:
            # Nadie esperará ya esta petición: si falla, que quede en el log
            in_progress.add_done_callback(self._record_failure)
            raise

    def _record_failure(self, task: asyncio.Future[dict[str, Any]]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "Falló el turno de %s cuya respuesta ya no se esperaba",
                self.call_id,
                exc_info=error,
            )

    def on_finished_speaking(self, delivery: dict[str, Any]) -> tuple[Action, list[str]]:
        """`delivery`: la `last_delivery` que devuelve la API tras registrar lo que sonó."""
        if not self.finished:
            return "proceed", []
        cut_pieces = [
            p["text"]
            for p in delivery.get("pieces") or []
            if p["importance"] == "notice" and p["state"] in ("not_heard", "not_delivered")
        ]
        return ("repeat_and_hang_up", cut_pieces) if cut_pieces else ("hang_up", [])
