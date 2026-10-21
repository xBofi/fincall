"""Ledger de entrega: qué parte del mensaje del bot oyó el vecino y qué parte no.

El mensaje del turno se trocea en piezas (frases) con su importancia. El transporte informa de
hasta dónde sonó (`texto_entregado`, o un ratio) y cada pieza queda oída o no. El turno
siguiente decide con eso: una pregunta o un aviso no oídos se recuperan; la cortesía se pierde.
Spec: `docs/superpowers/specs/2026-10-21-livekit-delivery-ledger-design.md`.
"""

from __future__ import annotations

import re
from typing import Any, Literal, TypedDict

Importancia = Literal["pregunta", "aviso", "dato", "cortesia"]
EstadoPieza = Literal["entregada", "oida", "no_oida", "no_entregada"]

#: Una pieza cortada cuenta como oída a partir de esta fracción de sus palabras
UMBRAL_OIDA = 0.6

_FRASE = re.compile(r"[^.!?]*[.!?]+|[^.!?]+$")


class Pieza(TypedDict):
    texto: str
    importancia: Importancia
    inicio: int
    fin: int


class PiezaEntregada(Pieza):
    estado: EstadoPieza
    texto_oido: str


def trocear(texto: str, avisos: list[str]) -> list[Pieza]:
    """Frases del mensaje con su importancia. `avisos`: textos que salieron de plantillas de
    aviso en este turno (el aviso de IA y grabación, el siguiente paso del cierre)."""
    piezas: list[Pieza] = []
    for m in _FRASE.finditer(texto):
        frase = m.group().strip()
        if not frase:
            continue
        inicio = m.start() + (len(m.group()) - len(m.group().lstrip()))
        if "¿" in frase or "?" in frase:
            importancia: Importancia = "pregunta"
        elif len(frase.split()) <= 3:  # "Gracias.", "Hasta luego.": también dentro de un aviso
            importancia = "cortesia"
        elif any(frase in aviso for aviso in avisos):
            importancia = "aviso"
        else:
            importancia = "dato"
        piezas.append(
            Pieza(texto=frase, importancia=importancia, inicio=inicio, fin=inicio + len(frase))
        )
    # Lo que va detrás de la última pregunta dice cómo contestarla ("Dígame la calle y el
    # número", "Responda sí o no"): es parte de la pregunta
    ultima = max((i for i, p in enumerate(piezas) if p["importancia"] == "pregunta"), default=None)
    if ultima is not None:
        for p in piezas[ultima + 1 :]:
            if p["importancia"] == "dato":
                p["importancia"] = "pregunta"
    return piezas


def evaluar(piezas: list[Pieza], corte: int) -> list[PiezaEntregada]:
    """Estado de cada pieza si el audio sonó hasta el carácter `corte` del mensaje."""
    evaluadas: list[PiezaEntregada] = []
    for p in piezas:
        if corte >= p["fin"]:
            estado: EstadoPieza = "entregada"
            oido = p["texto"]
        elif corte <= p["inicio"]:
            estado, oido = "no_entregada", ""
        else:
            oido = p["texto"][: corte - p["inicio"]].rstrip()
            fraccion = len(oido.split()) / max(1, len(p["texto"].split()))
            estado = "oida" if fraccion >= UMBRAL_OIDA else "no_oida"
        evaluadas.append(PiezaEntregada(**p, estado=estado, texto_oido=oido))
    return evaluadas


def corte_por_palabras(texto: str, oido: str) -> int:
    """Carácter de `texto` hasta el que llega `oido`, contando palabras.

    LiveKit devuelve las palabras sincronizadas con el audio, que no siempre conservan la
    puntuación ni los espacios del texto original: se cuenta cuántas sonaron y se corta el texto
    tras esa misma palabra."""
    n = len(re.findall(r"\w+", oido))
    if n == 0:
        return 0
    palabras = list(re.finditer(r"\w+", texto))
    if n >= len(palabras):
        return len(texto)
    return palabras[n - 1].end()


def _no_oidas(entrega: dict[str, Any] | None, importancia: Importancia) -> list[dict[str, Any]]:
    piezas = (entrega or {}).get("piezas") or []
    return [
        p
        for p in piezas
        if p["importancia"] == importancia and p["estado"] in ("no_oida", "no_entregada")
    ]


def avisos_no_oidos(entrega: dict[str, Any] | None) -> list[str]:
    return [p["texto"] for p in _no_oidas(entrega, "aviso")]


def confirmacion_no_valida(entrega: dict[str, Any] | None) -> bool:
    """Un sí/no suelto no responde a lo que no oyó: un dato sin oír o una pregunta que ni
    empezó. Si oyó los datos y cortó la pregunta al final, contesta a sabiendas."""
    if _no_oidas(entrega, "dato"):
        return True
    piezas = (entrega or {}).get("piezas") or []
    return any(p["importancia"] == "pregunta" and p["estado"] == "no_entregada" for p in piezas)


def texto_oido_si_cortado(entrega: dict[str, Any] | None) -> str | None:
    """Lo que llegó a sonar, solo si el mensaje no sonó entero."""
    piezas = (entrega or {}).get("piezas") or []
    if not piezas or all(p["estado"] == "entregada" for p in piezas):
        return None
    return " ".join(p["texto_oido"] for p in piezas if p["texto_oido"])
