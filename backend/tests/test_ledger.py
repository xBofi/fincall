"""Ledger de entrega: qué parte de cada mensaje oyó el vecino y qué hace el turno siguiente."""

from __future__ import annotations

from typing import Any

from building_call.graph.ledger import evaluar, trocear
from building_call.servicios import GestorLlamadas

SALUDO = (
    "Hola, le atiende un asistente de inteligencia artificial de su comunidad. Esta llamada "
    "puede ser grabada. ¿En qué finca se encuentra?"
)


async def _conversar(gestor: GestorLlamadas, turnos: list[str]) -> dict:
    estado = await gestor.iniciar()
    for texto in turnos:
        estado = await gestor.mensaje(estado["llamada_id"], texto)
    return estado


def _ultima_interpretacion(llm: Any) -> str:
    return [usuario for titulo, usuario in llm.llamadas if titulo == "interpretacion"][-1]


def _cortar(gestor: GestorLlamadas, estado: dict, oido: str) -> None:
    """Como el navegador: el mensaje del turno sonó hasta `oido` y el vecino lo cortó."""
    texto = estado["ultimo_mensaje_bot"]
    assert texto.startswith(oido)
    gestor.registrar_entrega(
        estado["llamada_id"], estado["turno"], "interrumpida", len(oido) / len(texto), oido
    )


# ----------------------------------------------------------------------------- piezas


def test_trocear_separa_por_frases_y_marca_la_importancia() -> None:
    avisos = [
        "Hola, le atiende un asistente de inteligencia artificial de su comunidad. "
        "Esta llamada puede ser grabada."
    ]
    piezas = trocear("Perfecto. " + SALUDO, avisos)
    assert [p["importancia"] for p in piezas] == ["cortesia", "aviso", "aviso", "pregunta"]
    assert piezas[3]["texto"] == "¿En qué finca se encuentra?"
    texto = "Perfecto. " + SALUDO
    assert all(texto[p["inicio"] : p["fin"]] == p["texto"] for p in piezas)


def test_evaluar_corte_a_mitad_de_pieza() -> None:
    texto = "Le resumo: gotea el grifo del patio comunitario. ¿Es correcto?"
    piezas = trocear(texto, [])
    # Oye "Le resumo: gotea el" (4 de 8 palabras): menos del 60 %, cuenta como no oída
    evaluadas = evaluar(piezas, len("Le resumo: gotea el"))
    assert [p["estado"] for p in evaluadas] == ["no_oida", "no_entregada"]
    assert evaluadas[0]["texto_oido"] == "Le resumo: gotea el"
    # Con "...el grifo" ya son 5 de 8: oída
    evaluadas = evaluar(piezas, len("Le resumo: gotea el grifo"))
    assert evaluadas[0]["estado"] == "oida"
    assert evaluar(piezas, len(texto))[1]["estado"] == "entregada"


# ----------------------------------------------------------------------------- el turno siguiente


async def test_si_tras_resumen_no_oido_no_confirma_y_se_repite(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    resumen = estado["ultimo_mensaje_bot"]
    assert resumen.startswith("Le resumo")
    _cortar(gestor, estado, "Le resumo: ")
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert estado.get("confirmado") is not True
    assert resumen in estado["mensajes_bot"][-1]


async def test_si_sobre_el_final_de_la_pregunta_confirma(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    texto = estado["ultimo_mensaje_bot"]
    _cortar(gestor, estado, texto[: texto.index("¿") + 3])  # "¿Es" y el vecino ya dice sí
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert estado["resultado"] == "incidencia_creada"


async def test_pregunta_de_finca_no_oida_y_si_suelto_repite(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12"])
    _cortar(gestor, estado, "He identificado ")
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert not estado.get("finca_id")
    assert "He identificado" in estado["mensajes_bot"][-1]


async def test_aviso_no_oido_se_dice_antes_de_la_respuesta(gestor: GestorLlamadas) -> None:
    estado = await gestor.iniciar()
    _cortar(gestor, estado, "Hola, le atiende un asistente")
    estado = await gestor.mensaje(estado["llamada_id"], "Calle Mayor 12")
    dicho = " ".join(estado["mensajes_bot"])
    assert "inteligencia artificial" in dicho and "grabada" in dicho
    assert dicho.index("grabada") < dicho.index("He identificado")
    # Ya se ha dicho: no se vuelve a anteponer en el turno siguiente
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert "grabada" not in " ".join(estado["mensajes_bot"])


async def test_cortesia_no_oida_no_cambia_nada(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A"])
    texto = estado["ultimo_mensaje_bot"]
    assert texto.startswith("Perfecto. ")
    gestor.registrar_entrega(estado["llamada_id"], estado["turno"], "completada", 1.0, texto)
    estado = await gestor.mensaje(estado["llamada_id"], "Gotea el grifo")
    assert estado["nodo_actual"] == "confirmar_resumen"


async def test_el_modelo_sabe_lo_que_oyo_el_vecino(gestor: GestorLlamadas, llm: Any) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A"])
    gestor.registrar_entrega(
        estado["llamada_id"], estado["turno"], "completada", 1.0, estado["ultimo_mensaje_bot"]
    )
    await gestor.mensaje(estado["llamada_id"], "Gotea el grifo del patio")
    assert "solo llegó a oír" not in _ultima_interpretacion(llm)

    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A"])
    _cortar(gestor, estado, "Perfecto. Cuéntem")
    await gestor.mensaje(estado["llamada_id"], "Gotea el grifo del patio")
    assert 'solo llegó a oír: "Perfecto. Cuéntem"' in _ultima_interpretacion(llm)


def test_entrega_registra_las_piezas(gestor: GestorLlamadas) -> None:
    import asyncio

    estado = asyncio.run(gestor.iniciar())
    _cortar(gestor, estado, "Hola, le atiende un asistente")
    piezas = gestor.obtener(estado["llamada_id"])["entrega_ultima"]["piezas"]
    assert {p["importancia"] for p in piezas} >= {"aviso", "pregunta"}
    assert piezas[-1]["estado"] == "no_entregada"


async def test_si_con_datos_tras_resumen_no_oido_no_confirma(
    gestor: GestorLlamadas, llm: Any
) -> None:
    # Con Gemini, el aviso de lo oído en el prompt no basta: devolvía confirma=true (2026-10-21)
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    _cortar(gestor, estado, "Le resumo: ")
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {  # type: ignore[method-assign]
        "confirma": True,
        "problema": "sale agua por el techo",
    }
    estado = await gestor.mensaje(
        estado["llamada_id"], "Sí, sí, y además ahora sale agua por el techo"
    )
    llm._interpretar = original  # type: ignore[method-assign]
    assert estado.get("resultado") != "incidencia_creada"
    assert estado["nodo_actual"] == "confirmar_resumen"
