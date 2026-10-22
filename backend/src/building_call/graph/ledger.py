"""Ledger de entrega: qué parte del mensaje del bot oyó el vecino y qué parte no.

El mensaje del turno se trocea en piezas (frases) con su importancia. El transporte informa de
hasta dónde sonó (`delivered_text`, o un ratio) y cada pieza queda oída o no. El turno
siguiente decide con eso: una pregunta o un aviso no oídos se recuperan; la cortesía se pierde.
Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md`.
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

Importance = Literal["question", "notice", "fact", "courtesy"]
PieceState = Literal["delivered", "heard", "not_heard", "not_delivered"]

#: Una pieza cortada cuenta como oída a partir de esta fracción de sus palabras
HEARD_THRESHOLD = 0.6

_PHRASE = re.compile(r"[^.!?]*[.!?]+|[^.!?]+$")


class Piece(TypedDict):
    text: str
    importance: Importance
    start: int
    end: int


class DeliveredPiece(Piece):
    state: PieceState
    heard_text: str


def split_pieces(text: str, notices: list[str]) -> list[Piece]:
    """Frases del mensaje con su importancia. `notices`: textos que salieron de plantillas de
    aviso en este turno (el aviso de IA y grabación, el siguiente paso del cierre)."""
    pieces: list[Piece] = []
    for m in _PHRASE.finditer(text):
        phrase = m.group().strip()
        if not phrase:
            continue
        start = m.start() + (len(m.group()) - len(m.group().lstrip()))
        if "¿" in phrase or "?" in phrase:
            importance: Importance = "question"
        elif len(phrase.split()) <= 3:  # "Gracias.", "Hasta luego.": también dentro de un aviso
            importance = "courtesy"
        elif any(phrase in notice for notice in notices):
            importance = "notice"
        else:
            importance = "fact"
        pieces.append(
            Piece(text=phrase, importance=importance, start=start, end=start + len(phrase))
        )
    # Lo que va detrás de la última pregunta dice cómo contestarla ("Dígame la calle y el
    # número", "Responda sí o no"): es parte de la pregunta
    last = max((i for i, p in enumerate(pieces) if p["importance"] == "question"), default=None)
    if last is not None:
        for p in pieces[last + 1 :]:
            if p["importance"] == "fact":
                p["importance"] = "question"
    return pieces


def evaluate(pieces: list[Piece], cut: int) -> list[DeliveredPiece]:
    """Estado de cada pieza si el audio sonó hasta el carácter `cut` del mensaje."""
    evaluated: list[DeliveredPiece] = []
    for p in pieces:
        if cut >= p["end"]:
            state: PieceState = "delivered"
            heard_text = p["text"]
        elif cut <= p["start"]:
            state, heard_text = "not_delivered", ""
        else:
            heard_text = p["text"][: cut - p["start"]].rstrip()
            fraction = len(heard_text.split()) / max(1, len(p["text"].split()))
            state = "heard" if fraction >= HEARD_THRESHOLD else "not_heard"
        evaluated.append(DeliveredPiece(**p, state=state, heard_text=heard_text))
    return evaluated


def cut_by_words(text: str, heard_text: str) -> int:
    """Carácter de `text` hasta el que llega `heard_text`, contando palabras.

    LiveKit devuelve las palabras sincronizadas con el audio, que no siempre conservan la
    puntuación ni los espacios del texto original: se cuenta cuántas sonaron y se corta el texto
    tras esa misma palabra."""
    n = len(re.findall(r"\w+", heard_text))
    if n == 0:
        return 0
    words = list(re.finditer(r"\w+", text))
    if n >= len(words):
        return len(text)
    return words[n - 1].end()


def _not_heard(delivery: dict[str, Any] | None, importance: Importance) -> list[dict[str, Any]]:
    pieces = (delivery or {}).get("pieces") or []
    return [
        p
        for p in pieces
        if p["importance"] == importance and p["state"] in ("not_heard", "not_delivered")
    ]


def unheard_notices(delivery: dict[str, Any] | None) -> list[str]:
    return [p["text"] for p in _not_heard(delivery, "notice")]


def invalid_confirmation(delivery: dict[str, Any] | None) -> bool:
    """Un sí/no suelto no responde a lo que no oyó: un dato sin oír o una pregunta que ni
    empezó. Si oyó los datos y cortó la pregunta al final, contesta a sabiendas."""
    if _not_heard(delivery, "fact"):
        return True
    pieces = (delivery or {}).get("pieces") or []
    return any(p["importance"] == "question" and p["state"] == "not_delivered" for p in pieces)


def heard_text_if_cut(delivery: dict[str, Any] | None) -> str | None:
    """Lo que llegó a sonar, solo si el mensaje no sonó entero."""
    pieces = (delivery or {}).get("pieces") or []
    if not pieces or all(p["state"] == "delivered" for p in pieces):
        return None
    return " ".join(p["heard_text"] for p in pieces if p["heard_text"])
