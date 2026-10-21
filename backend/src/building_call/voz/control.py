"""Qué hace el agente de voz cuando termina de sonar un mensaje. Sin LiveKit: se prueba solo.

Durante la llamada el turno siguiente lo decide el grafo con el ledger. Al final no hay turno
siguiente: si el vecino cortó el cierre, el agente repite lo que no oyó del aviso (el siguiente
paso) antes de colgar. Confirmado por Andreu el 2026-10-21: el siguiente paso es importante.
"""

from __future__ import annotations

from typing import Any, Literal

Accion = Literal["seguir", "colgar", "repetir_y_colgar"]


class ControlLlamada:
    def __init__(self, llamada_id: str) -> None:
        self.llamada_id = llamada_id
        self.turno = 0
        self.finalizada = False
        self.texto = ""

    def nuevo_turno(self, vista: dict[str, Any]) -> str:
        """Guarda el turno que va a sonar y devuelve su texto."""
        self.turno = int(vista.get("turno", 0))
        self.finalizada = bool(vista.get("finalizada"))
        self.texto = " ".join(vista.get("mensajes_bot") or []).strip()
        return self.texto

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
