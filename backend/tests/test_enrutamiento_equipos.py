"""A quién llega cada incidencia: mantenedor del equipo, especialista o administrador.

Regla de Andreu (2026-10-21, Provisional): solo se avisa directamente a un profesional cuando es
una reparación, hay alguien para ella y el vecino confirma sin objeciones. Lo demás lo decide el
administrador, con una sugerencia. Spec: 2026-10-21-equipment-inventory-and-routing-design.md.
"""

from __future__ import annotations

from typing import Any

from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas

INICIO = ["Calle Mayor 12", "sí", "1 A"]


async def _conversar(gestor: GestorLlamadas, turnos: list[str]) -> dict:
    estado = await gestor.iniciar()
    for texto in turnos:
        estado = await gestor.mensaje(estado["llamada_id"], texto)
    return estado


async def _incidencia(repo: RepositorioMemoria) -> Any:
    incidencias = await repo.listar_incidencias()
    assert len(incidencias) == 1
    return incidencias[0]


async def test_el_resumen_no_lee_la_categoria_ni_la_urgencia(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, [*INICIO, "Gotea el grifo del patio"])
    resumen = estado["mensajes_bot"][-1]
    assert resumen.startswith("Le resumo")
    assert "clasificado" not in resumen and "urgencia" not in resumen


async def test_una_fuga_va_al_fontanero_como_siempre(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(gestor, [*INICIO, "Gotea el grifo del patio", "sí"])
    inc = await _incidencia(repo)
    assert inc.especialista_id == "esp-fontanero" and inc.actuacion == "reparacion"
    assert "Fontanería Pérez" in estado["mensajes_bot"][-1]


async def test_averia_de_un_equipo_va_a_su_mantenedor(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(gestor, [*INICIO, "El portero automático no funciona", "sí"])
    inc = await _incidencia(repo)
    assert inc.equipo_id == "eq-mayor12-portero"
    assert inc.especialista_id == "esp-porteros"
    assert "Fermax" in (inc.sugerencia or "") and "PL-2024-118" in (inc.sugerencia or "")
    assert "Porteros Levante" in estado["mensajes_bot"][-1]


async def test_vandalismo_va_al_administrador_con_sugerencia(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(
        gestor, [*INICIO, "Han vandalizado el portero automático, lo han reventado", "sí"]
    )
    inc = await _incidencia(repo)
    assert inc.especialista_id is None and inc.estado == "pendiente"
    assert inc.actuacion == "siniestro" and inc.equipo_id == "eq-mayor12-portero"
    assert "seguro" in (inc.sugerencia or "") and "Porteros Levante" in (inc.sugerencia or "")
    cierre = estado["mensajes_bot"][-1]
    assert "administrador" in cierre and "Porteros Levante" not in cierre


async def test_si_pero_con_objecion_va_al_administrador(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(
        gestor,
        [*INICIO, "No funciona la luz del portal", "Sí, pero eso no lo arregla un electricista"],
    )
    assert estado["resultado"] == "incidencia_creada"
    inc = await _incidencia(repo)
    assert inc.especialista_id is None
    assert "no lo arregla un electricista" in (inc.sugerencia or "")
    assert "Electricidad Gómez" in (inc.sugerencia or "")  # lo que habría hecho el bot


async def test_el_equipo_se_reconoce_aunque_la_averia_se_cuente_antes_de_la_finca(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    await _conversar(
        gestor, ["Calle Mayor 12, el portero automático no funciona", "sí", "1 A", "sí"]
    )
    inc = await _incidencia(repo)
    assert inc.equipo_id == "eq-mayor12-portero" and inc.especialista_id == "esp-porteros"


async def test_el_detalle_pregunta_por_el_equipo_no_por_la_categoria(
    gestor_detalle: GestorLlamadas,
) -> None:
    estado = await _conversar(gestor_detalle, [*INICIO, "El portero automático no funciona"])
    assert estado["nodo_actual"] == "pedir_detalle"
    pregunta = estado["mensajes_bot"][-1]
    assert "portero automático" in pregunta and "electricista" not in pregunta


async def test_un_siniestro_no_pide_detalle(gestor_detalle: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor_detalle, [*INICIO, "Han vandalizado el portero automático, lo han reventado"]
    )
    assert estado["nodo_actual"] == "confirmar_resumen"
