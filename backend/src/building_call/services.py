"""Gestor de sesiones de llamada: mantiene el estado entre turnos, invoca el grafo y mide."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter, time
from typing import Any, cast
from uuid import uuid4

from building_call import i18n
from building_call.domain.entities import Call, CallResult
from building_call.graph.ledger import evaluate, split_pieces, unheard_notices
from building_call.graph.nodes import Dependencies
from building_call.graph.registry import get_graph
from building_call.graph.state import CallState, Trace, initial_state
from building_call.i18n import detection, personas
from building_call.llm.phrasing import phrase
from building_call.metrics import MetricsRegistry, TurnTimings
from building_call.repos.call_state import CallStateStore, MemoryStateStore

logger = logging.getLogger(__name__)


class CallNotFound(KeyError):
    pass


def _phrasing_data(state: CallState) -> dict[str, Any]:
    return {
        "estate": state.get("estate_address"),
        "home": state.get("home_label"),
        "problem": state.get("summary") or state.get("request"),
        "category": state.get("category"),
        "urgency": state.get("urgency"),
        "node": state.get("current_node"),
    }


def _deserves_phrasing(state: CallState, utterance: str | None) -> bool:
    """Redactar cuesta una llamada LLM (~0,5-1 s). Solo cuando aporta naturalidad: el vecino ha
    dicho algo que no era la respuesta esperada (turno interpretado por LLM o intención global)
    y el mensaje no es un cierre largo lleno de hechos (ahí la plantilla ya es completa)."""
    if utterance is None or state.get("finished"):
        return False
    if state.get("current_node") == "handle_emergency":
        # La indicación de seguridad se dice tal cual: la comprobación de hechos de la redacción
        # solo vigila números y nombres, y podría perderse «no encienda luces»
        return False
    timings = state.get("turn_timings", {})
    if timings.get("interpretation_origin") != "llm":
        return False
    return len(" ".join(state.get("bot_messages", []))) <= 260


def _prepend_notices(fresh: CallState, notices: list[str]) -> list[str]:
    """Los avisos que el vecino cortó en el turno anterior van delante de la respuesta, una vez.
    Devuelve los que se han antepuesto."""
    said = " ".join(fresh.get("bot_messages", []))
    missing = [a for a in notices if a not in said]
    if not missing or not fresh.get("bot_messages"):
        return []
    primero = fresh["bot_messages"][0]
    with_notices = " ".join([*missing, primero])
    fresh["bot_messages"] = [with_notices, *fresh["bot_messages"][1:]]
    history = list(fresh.get("history", []))
    for i in range(len(history) - 1, -1, -1):
        if history[i]["role"] == "bot" and history[i]["text"] == primero:
            history[i] = {"role": "bot", "text": with_notices}
            break
    fresh["history"] = history
    return missing


class CallManager:
    # El expediente se persiste en cada turno; el estado vivo solo hace falta mientras la llamada
    # sigue (y un margen para las últimas entregas de audio de la consola)
    FINISHED_RETENTION_S = 15 * 60
    ABANDONED_RETENTION_S = 2 * 60 * 60

    def __init__(
        self,
        deps: Dependencies,
        default_graph: str = "standard",
        metrics: MetricsRegistry | None = None,
        phraser: Any | None = None,
        clock: Callable[[], float] = time,
        store: CallStateStore | None = None,
    ) -> None:
        """`phraser`: modelo que reformula las respuestas del grafo (paráfrasis controlada).
        None = plantillas tal cual.

        `store`: dónde vive el estado de las llamadas en curso. Por defecto, el del repositorio
        (`repo.call_state_store`: PostgreSQL en el modo real, así una llamada sobrevive a un
        reinicio y la puede continuar otra instancia); si el repositorio no trae, uno en memoria.
        `clock`: segundos de reloj de pared; el almacén los compara entre procesos."""
        self.deps = deps
        self.phraser = phraser
        self.default_graph = default_graph
        self.metrics = metrics or MetricsRegistry()
        self.store: CallStateStore = (
            store or getattr(deps.repo, "call_state_store", None) or MemoryStateStore()
        )
        self._graphs: dict[str, Any] = {}
        # Copia local del último estado conocido de cada llamada de este proceso, para lo que no
        # puede esperar a la base (la especulación sobre parciales del STT, síncrona). Los turnos
        # siempre parten de lo que hay en el almacén.
        self._local: dict[str, CallState] = {}
        self._last_use: dict[str, float] = {}
        self._last_purge = float("-inf")
        self._clock = clock

    async def _save_state(self, state: CallState) -> bool:
        """Devuelve False si no se guardó porque en el almacén ya había un estado más nuevo
        (otro turno o una entrega que llegó a la vez, quizá en otra instancia)."""
        now = self._clock()
        saved = await self.store.save(state, now)
        if saved:
            self._remember(state, now)
        else:
            logger.warning(
                "Estado de la llamada %s (turno %s) descartado: ya había uno más nuevo",
                state["call_id"],
                state.get("turn"),
            )
        if now - self._last_purge >= self.store.purge_interval_s:
            self._last_purge = now
            await self._purge(now)
        return saved

    def _remember(self, state: CallState, now: float) -> None:
        self._local[state["call_id"]] = state
        self._last_use[state["call_id"]] = now

    def _forget(self, call_id: str) -> None:
        self._local.pop(call_id, None)
        self._last_use.pop(call_id, None)
        self.deps.discard_speculations(call_id)

    async def _purge(self, now: float) -> None:
        """Misma retención en el almacén y en la copia local (que puede tener llamadas que ya
        purgó otra instancia)."""
        try:
            expired_calls = set(
                await self.store.purge(
                    now, self.FINISHED_RETENTION_S, self.ABANDONED_RETENTION_S
                )
            )
        except Exception:  # noqa: BLE001 - una purga fallida no rompe el turno; ya se reintentará
            logger.exception("No se pudo purgar el estado de las llamadas caducadas")
            expired_calls = set()
        for call_id, usage in self._last_use.items():
            idle_time = now - usage
            if idle_time > self.ABANDONED_RETENTION_S or (
                idle_time > self.FINISHED_RETENTION_S
                and self._local[call_id].get("finished")
            ):
                expired_calls.add(call_id)
        for call_id in expired_calls:
            self._forget(call_id)

    def _graph(self, name: str) -> Any:
        if name not in self._graphs:
            self._graphs[name] = get_graph(name, self.deps)
        return self._graphs[name]

    async def _save_operation(self, state: CallState) -> None:
        """Persiste el expediente tras cada turno, también mientras sigue activo."""
        finished = bool(state.get("finished"))
        result = state.get("result")
        await self.deps.repo.save_call(
            Call(
                id=state["call_id"],
                started_at=datetime.fromisoformat(state["started_at"]),
                caller_phone=state.get("caller_phone"),
                estate_id=state.get("estate_id"),
                home_id=state.get("home_id"),
                transcript=state.get("history", []),
                summary=state.get("summary") or state.get("request"),
                result=cast("CallResult | None", result),
                review_reason=state.get("review_reason"),
                config_id=state.get("config_id"),
                graph=state.get("graph"),
                state="finished" if finished else "active",
                finished_at=datetime.now(UTC) if finished else None,
                needs_review=finished and result != "incident_created",
            )
        )

    async def _run(
        self,
        state: CallState,
        utterance: str | None,
        stt_ms: int | None = None,
        *,
        reprompt: bool = False,
    ) -> CallState:
        """One turn of the graph. `reprompt`: the caller's words are already in the history
        (they asked for another language) and the current node asks its question again."""
        # El grafo a usar puede cambiar al resolver la configuración de la finca
        graph_name = (
            (state.get("config") or {}).get("graph")
            or state.get("graph")
            or self.default_graph
        )
        turn = state.get("turn", 0) + (1 if utterance is not None or reprompt else 0)
        initial_node = state.get("current_node", "next_step")
        prepared: CallState = {
            **state,
            "user_utterance": utterance,
            "bot_messages": [],
            "awaiting_user": False,
            "graph": graph_name,
            "turn": turn,
            "turn_timings": {},
            "turn_notices": [],
        }
        t0 = perf_counter()
        # Everything the graph says or reads in this turn is in the call's language
        language = state.get("language") or i18n.default_code()
        persona = state.get("persona")
        with i18n.use(language), personas.use(persona):
            fresh: CallState = await self._graph(graph_name).ainvoke(prepared)
        timings = dict(fresh.get("turn_timings", {}))
        if (
            self.phraser is not None
            and fresh.get("bot_messages")
            and _deserves_phrasing(fresh, utterance)
        ):
            template = " ".join(fresh["bot_messages"])
            with i18n.use(language), personas.use(persona):
                text, metric = await phrase(
                    self.phraser, template, utterance, _phrasing_data(fresh)
                )
            timings.update(metric)
            if text != template:
                fresh["bot_messages"] = [text]
                # El historial guarda lo que realmente se dijo
                # Los mensajes del grafo de este turno son los últimos del historial: se
                # sustituyen por la redacción que realmente se dijo
                history = list(fresh.get("history", []))
                while (
                    history
                    and history[-1]["role"] == "bot"
                    and history[-1]["text"] in template
                ):
                    history.pop()
                history.append({"role": "bot", "text": text})
                fresh["history"] = history
        graph_ms = int((perf_counter() - t0) * 1000)
        server_ms = graph_ms + (stt_ms or 0)
        self.metrics.record(
            TurnTimings(
                call_id=fresh["call_id"],
                turn=turn,
                initial_node=initial_node,
                final_node=fresh.get("current_node", ""),
                stt_ms=stt_ms,
                interpretation_ms=timings.get("interpretation_ms"),
                classification_ms=timings.get("classification_ms"),
                llm_calls=int(timings.get("llm_calls", 0)),
                graph_ms=graph_ms,
                server_ms=server_ms,
                interpretation_origin=timings.get("interpretation_origin", "none"),
            )
        )
        notices = list(fresh.get("turn_notices", []))
        if utterance is not None:
            notices += _prepend_notices(fresh, unheard_notices(state.get("last_delivery")))
        if fresh.get("bot_messages"):
            fresh["last_bot_message"] = " ".join(fresh["bot_messages"])
            fresh["bot_pieces"] = [
                dict(p) for p in split_pieces(" ".join(fresh["bot_messages"]), notices)
            ]
            fresh["last_delivery"] = None  # el navegador informará de lo que llegó a sonar
        timings.update(graph_ms=graph_ms, server_ms=server_ms, turn=turn)
        if stt_ms is not None:
            timings["stt_ms"] = stt_ms
        fresh["turn_timings"] = timings
        await self._save_state(fresh)
        await self._save_operation(fresh)
        await self._save_traceability(state, fresh, utterance, timings)
        return fresh

    async def _save_traceability(
        self,
        previous: CallState,
        fresh: CallState,
        utterance: str | None,
        timings: dict[str, Any],
    ) -> None:
        """Turno (qué dijo cada parte, cómo se interpretó, cuánto tardó) y decisiones del grafo
        (las entradas de traza nuevas en este turno), para el dossier de la consola operativa."""
        repo: Any = self.deps.repo
        if not hasattr(repo, "save_turn"):
            return
        turn = fresh.get("turn", 0)
        await repo.save_turn(
            fresh["call_id"],
            turn,
            {
                "caller": utterance,
                "assistant": fresh.get("bot_messages", []),
                "initial_node": previous.get("current_node"),
                "final_node": fresh.get("current_node"),
                "interpretation": fresh.get("interpretation"),
                "timings": timings,
            },
        )
        prior_count = len(previous.get("trace", []))
        for step in fresh.get("trace", [])[prior_count:]:
            await repo.save_decision(
                fresh["call_id"],
                turn,
                step["node"],
                {"action": step["action"], "detail": step["detail"]},
            )

    async def begin(
        self,
        caller_phone: str | None = None,
        language: str | None = None,
        persona: str | None = None,
    ) -> CallState:
        """Opens a call. `language`: chosen for this call (number, stream parameter, console);
        None = `BC_LANGUAGE`, and the caller's first words may switch it (detection).
        `persona`: the client's voice persona; None = `BC_PERSONA`. Unknown values raise
        `i18n.UnknownLanguage` / `personas.UnknownPersona`."""
        call_id = f"llam-{uuid4().hex[:12]}"
        code = i18n.get(language).code if language else i18n.default_code()
        if persona is not None:
            personas.get(persona)
        state = initial_state(
            call_id,
            datetime.now(UTC).isoformat(),
            self.default_graph,
            language=code,
            locked=language is not None,
        )
        state["caller_phone"] = caller_phone
        state["persona"] = persona or personas.default_id()
        return await self._run(state, None)

    async def message(
        self,
        call_id: str,
        text: str,
        stt_ms: int | None = None,
        stt_language: str | None = None,
        stt_confidence: float | None = None,
    ) -> CallState:
        """A caller turn. `stt_language` / `stt_confidence`: what the STT detected, if it
        reports it (AssemblyAI with `language_detection`)."""
        state = await self.get(call_id)
        if state.get("finished"):
            return state
        state, requested = self._language_for_turn(state, text, stt_language, stt_confidence)
        if requested:
            # "English, please": the question is asked again, now in English
            state = {
                **state,
                "history": [*state.get("history", []), {"role": "caller", "text": text}],
            }
            return await self._run(state, None, stt_ms, reprompt=True)
        return await self._run(state, text, stt_ms)

    def _language_for_turn(
        self,
        state: CallState,
        text: str,
        stt_language: str | None,
        stt_confidence: float | None,
    ) -> tuple[CallState, bool]:
        """Switches the call's language if the caller asks for another one, or, in the first
        turns of a call whose language nobody chose, if the detection is confident enough.
        Returns (state, asked_for_it)."""
        from building_call.settings import settings

        current = state.get("language") or i18n.default_code()
        enabled = i18n.enabled()
        asked = detection.requested_language(text, current, enabled)
        if asked is not None:
            return self._switch(state, asked, f"requested: {text[:40]}", locked=True), True
        caller_turns = sum(1 for h in state.get("history", []) if h["role"] == "caller")
        if (
            state.get("language_locked")
            or not settings.language_detection
            or caller_turns >= settings.language_detection_turns
        ):
            return state, False
        found = detection.decide(text, stt_language, stt_confidence, enabled)
        if (
            found.language
            and found.language != current
            and found.confidence >= settings.language_detection_threshold
        ):
            detail = f"detected ({found.source} {found.confidence:.2f})"
            return self._switch(state, found.language, detail, locked=True), False
        return state, False

    @staticmethod
    def _switch(state: CallState, language: str, why: str, *, locked: bool) -> CallState:
        old = state.get("language")
        logger.info("Llamada %s: idioma %s -> %s (%s)", state["call_id"], old, language, why)
        return {
            **state,
            "language": language,
            "language_locked": locked,
            "trace": [
                *state.get("trace", []),
                Trace(node="language", action="switch", detail=f"{old} -> {language}: {why}"),
            ],
        }

    async def close_on_failure(self, call_id: str, reason: str) -> CallState:
        """El asistente no puede seguir (LLM, STT o TTS caídos): se cierra la llamada y queda
        derivada al administrador y marcada para revisar, en vez de quedarse "activa"."""
        state = await self.get(call_id)
        if not state.get("finished"):
            state = {
                **state,
                "finished": True,
                "result": "handed_to_administrator",
                "review_reason": reason,
            }
            await self._save_state(state)
            await self._save_operation(state)
        return state

    async def record_delivery(
        self, call_id: str, turn: int, delivery_state: str, ratio: float, text: str | None
    ) -> CallState:
        """Ledger de salida: qué parte del último mensaje llegó a reproducirse al vecino."""
        state = await self.get(call_id)
        if turn != state.get("turn", 0):
            return state  # entrega de un turno ya superado: no altera el estado actual
        message = state.get("last_bot_message") or ""
        cut: int | None
        if delivery_state == "completed":
            cut = len(message)
        elif delivery_state == "not_played":
            cut = 0
        else:
            # Sin texto no hay medida (Twilio corta y anota un 0,5 fijo): sin piezas evaluadas
            # el turno siguiente se comporta como antes del ledger, en vez de decidir sobre una
            # posición inventada
            cut = len(text) if text is not None else None
        pieces = evaluate(state.get("bot_pieces", []), cut) if cut is not None else []  # type: ignore[arg-type]
        state = {
            **state,
            "last_delivery": {
                "state": delivery_state,
                "ratio": max(0.0, min(1.0, ratio)),
                "delivered_text": text,
                "pieces": [dict(p) for p in pieces],
            },
        }
        if not await self._save_state(state):
            # Entre leer y escribir avanzó la llamada: la entrega ya no aplica
            return await self.get(call_id)
        return state

    async def get(self, call_id: str) -> CallState:
        """Estado actual de la llamada según el almacén (lo comparten reinicios e instancias)."""
        state = await self.store.get(call_id)
        if state is None:
            if call_id in self._local:
                self._forget(call_id)
            raise CallNotFound(call_id)
        self._remember(state, self._last_use.get(call_id, self._clock()))
        return state

    def get_local(self, call_id: str) -> CallState:
        """Último estado que este proceso ha visto, sin ir al almacén. Solo para lo que no puede
        esperar ni bloquear (la especulación sobre parciales del STT); los turnos usan `get`."""
        try:
            return self._local[call_id]
        except KeyError as e:
            raise CallNotFound(call_id) from e
