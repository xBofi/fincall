"""Tratamiento especial de emergencias (gas, fuego, humo, alguien atrapado).

Bóveda (Confirmado por Andreu): las emergencias están desactivadas inicialmente y se tratan como
`alta`. Con el ajuste `emergencias_activas`, el bot da primero una indicación de seguridad, no
pide detalle ni lee el resumen largo, crea la incidencia como emergencia y deja el aviso al
coordinador y al teléfono oficial de la finca como simulado (nunca llama ni envía nada).
"""

from __future__ import annotations

import pytest

from building_call.graph.nodos import Dependencias, _indicacion_seguridad
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas, _merece_redaccion

INICIO = ["Calle Mayor 12", "sí", "tercero B"]


def _activar(repo: RepositorioMemoria, **otros: object) -> None:
    repo.config_administrador["adm-demo"] = repo.config_administrador["adm-demo"].model_copy(
        update={"emergencias_activas": True, **otros}
    )


async def _conversar(gestor: GestorLlamadas, turnos: list[str]) -> dict:
    estado = await gestor.iniciar()
    for texto in turnos:
        estado = await gestor.mensaje(estado["llamada_id"], texto)
    return estado


def _plantillas(estado: dict) -> list[str]:
    return [t["detalle"].split(" ")[0] for t in estado["traza"] if t["accion"] == "plantilla"]


# --------------------------------------------------------------------- desactivado (por defecto)


async def test_desactivado_la_emergencia_sigue_el_flujo_de_urgencia_alta(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(gestor, [*INICIO, "Huele a gas en el rellano del tercero"])
    assert estado["urgencia_declarada"] == "emergencia"
    assert estado["urgencia"] == "alta"
    assert estado["nodo_actual"] == "confirmar_resumen"
    dicho = " ".join(estado["mensajes_bot"])
    assert "112" not in dicho
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert estado["resultado"] == "incidencia_creada"
    assert estado["canal_aviso"] == "correo"  # regla de `alta`
    [incidencia] = await repo.listar_incidencias()
    assert incidencia.urgencia == "alta"
    assert not any(t["nodo"] == "atender_emergencia" for t in estado["traza"])


# --------------------------------------------------------------------- activado


async def test_activado_gas_da_indicacion_de_seguridad_y_confirma_lo_minimo(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(gestor, [*INICIO, "Huele a gas en el rellano del tercero"])
    assert estado["urgencia"] == "emergencia"
    assert estado["nodo_actual"] == "atender_emergencia"
    assert estado["esperando_usuario"] is True
    mensaje = estado["mensajes_bot"][-1]
    assert "112" in mensaje
    assert "no encienda luces" in mensaje.lower()
    assert "Calle Mayor 12" in mensaje
    assert mensaje.rstrip().endswith("?")
    # Ni resumen largo ni pregunta de detalle
    assert "Le resumo" not in mensaje
    assert "atender_emergencia.aviso" in _plantillas(estado)
    assert "atender_emergencia.seguridad_gas" in _plantillas(estado)
    # La indicación de seguridad es un aviso: si el vecino la corta, se vuelve a decir
    assert any("112" in a for a in estado["avisos_turno"])


async def test_activado_confirmada_crea_incidencia_de_emergencia_con_aviso_simulado(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(gestor, [*INICIO, "Huele a gas en el rellano del tercero", "sí"])
    assert estado["finalizada"] is True
    assert estado["resultado"] == "incidencia_creada"
    assert estado["confirmado"] is True
    [incidencia] = await repo.listar_incidencias()
    assert incidencia.urgencia == "emergencia"
    assert incidencia.sugerencia and "emergencia" in incidencia.sugerencia.lower()
    assert "simulad" in incidencia.sugerencia.lower()
    # Aviso por llamada al teléfono oficial de la finca: simulado, nunca real
    assert estado["canal_aviso"] == "llamada"
    assert estado["destino_aviso"] == "+34910000001"
    avisos = [t for t in estado["traza"] if t["accion"] == "aviso_emergencia_simulado"]
    assert len(avisos) == 1
    assert "+34910000001" in avisos[0]["detalle"]
    assert "mayor12@demo.test" in avisos[0]["detalle"]  # coordinador
    cierre = estado["mensajes_bot"][-1]
    assert "112" in cierre
    assert "avisar_canal.cierre_emergencia" in _plantillas(estado)
    assert "confirmar_resumen" not in {t["nodo"] for t in estado["traza"]}


async def test_activado_no_pide_detalle_aunque_la_finca_lo_tenga_activo(
    llm, repo: RepositorioMemoria
) -> None:
    _activar(repo, pedir_detalle=True)
    gestor = GestorLlamadas(Dependencias(repo=repo, llm=llm))
    estado = await _conversar(gestor, [*INICIO, "Huele a gas"])
    assert estado["nodo_actual"] == "atender_emergencia"
    assert estado["detalle_pedido"] is False


async def test_activado_fuego_o_humo_usa_la_indicacion_de_incendio(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(gestor, [*INICIO, "Sale mucho humo del cuarto de contadores"])
    assert estado["nodo_actual"] == "atender_emergencia"
    assert "atender_emergencia.seguridad_fuego" in _plantillas(estado)
    mensaje = estado["mensajes_bot"][-1].lower()
    assert "ascensor" in mensaje and "112" in mensaje


async def test_activado_persona_atrapada_usa_la_indicacion_general(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(gestor, [*INICIO, "El ascensor está parado y hay una señora dentro"])
    assert estado["nodo_actual"] == "atender_emergencia"
    assert "atender_emergencia.seguridad_general" in _plantillas(estado)
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    [incidencia] = await repo.listar_incidencias()
    assert incidencia.urgencia == "emergencia"
    assert incidencia.categoria == "ascensor"
    assert estado["canal_aviso"] == "llamada"


async def test_activado_si_el_vecino_dice_que_no_es_emergencia_se_trata_como_alta(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(
        gestor, [*INICIO, "Huele a gas en el rellano", "No, no es una emergencia"]
    )
    assert estado["urgencia"] == "alta"
    assert estado["nodo_actual"] == "confirmar_resumen"
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    [incidencia] = await repo.listar_incidencias()
    assert incidencia.urgencia == "alta"
    assert estado["canal_aviso"] == "correo"


async def test_activado_una_respuesta_dudosa_no_pierde_la_emergencia(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    """Ante la duda se registra: una emergencia nunca se queda en un bucle de repreguntas."""
    _activar(repo)
    estado = await _conversar(
        gestor, [*INICIO, "Huele a gas en el rellano", "Ay, madre mía, qué miedo"]
    )
    assert estado["finalizada"] is True
    [incidencia] = await repo.listar_incidencias()
    assert incidencia.urgencia == "emergencia"
    assert any(t["accion"] == "emergencia_sin_confirmacion" for t in estado["traza"])


async def test_activado_las_urgencias_normales_no_cambian(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(
        gestor, [*INICIO, "Hay una fuga de agua en el baño y está inundando el suelo"]
    )
    assert estado["urgencia"] == "alta"
    assert estado["nodo_actual"] == "confirmar_resumen"


@pytest.mark.parametrize(
    ("nodo", "esperado"), [("atender_emergencia", False), ("confirmar_resumen", True)]
)
def test_la_indicacion_de_seguridad_no_se_parafrasea(nodo: str, esperado: bool) -> None:
    """La redacción solo vigila números y nombres: podría perder «no encienda luces»."""
    estado = {
        "nodo_actual": nodo,
        "tiempos_turno": {"origen_interpretacion": "llm"},
        "mensajes_bot": ["Texto corto. ¿Es correcto?"],
    }
    assert _merece_redaccion(estado, "lo que sea") is esperado  # type: ignore[arg-type]


async def test_activado_si_corrige_la_finca_se_vuelve_a_identificar_sin_perder_la_emergencia(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    estado = await _conversar(
        gestor, [*INICIO, "Huele a gas en el rellano", "No, es en la avenida de la Alameda 5"]
    )
    assert estado["nodo_actual"] == "identificar_finca"
    assert estado["finca_id"] is None
    assert estado["urgencia"] is None
    assert estado["urgencia_declarada"] == "emergencia"
    assert estado["solicitud"]  # lo que contó no se pierde


async def test_activado_sin_telefono_en_la_finca_usa_el_del_contacto(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    _activar(repo)
    finca = repo.fincas["finca-mayor-12"]
    repo.fincas["finca-mayor-12"] = finca.model_copy(update={"telefono_oficial": ""})
    estado = await _conversar(gestor, [*INICIO, "Huele a gas en el rellano", "sí"])
    assert estado["canal_aviso"] == "llamada"
    assert estado["destino_aviso"] == "+34910000001"  # contacto de la configuración


@pytest.mark.parametrize(
    ("texto", "plantilla"),
    [
        ("Huele a quemado en el cuarto de contadores", "atender_emergencia.seguridad_fuego"),
        ("Se está incendiando el contenedor", "atender_emergencia.seguridad_fuego"),
        ("Hay una fuga de gas", "atender_emergencia.seguridad_gas"),
        ("Una señora atrapada en el ascensor", "atender_emergencia.seguridad_general"),
    ],
)
def test_indicacion_de_seguridad_por_palabras_clave(texto: str, plantilla: str) -> None:
    assert _indicacion_seguridad(texto).plantilla_id == plantilla  # type: ignore[attr-defined]
