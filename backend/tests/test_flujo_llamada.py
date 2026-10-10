from __future__ import annotations

from building_call.graph.registro import REGISTRO, obtener_grafo
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas


async def _conversar(gestor: GestorLlamadas, turnos: list[str]) -> dict:
    estado = await gestor.iniciar()
    for texto in turnos:
        estado = await gestor.mensaje(estado["llamada_id"], texto)
    return estado


async def test_saludo_inicial_pide_finca(gestor: GestorLlamadas) -> None:
    estado = await gestor.iniciar()
    assert estado["nodo_actual"] == "identificar_finca"
    assert estado["esperando_usuario"] is True
    assert "finca" in estado["mensajes_bot"][0].lower()


async def test_flujo_completo_crea_incidencia(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(
        gestor,
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
            "sí, correcto",
        ],
    )
    assert estado["finalizada"] is True
    assert estado["resultado"] == "incidencia_creada"
    assert estado["finca_id"] == "finca-mayor-12"
    assert estado["vivienda_id"] == "finca-mayor-12-u-3B"
    assert estado["nombre_declarado"] is None  # el nombre no se pregunta
    assert estado["categoria"] == "fontaneria"
    assert estado["urgencia"] == "alta"
    assert estado["especialista_id"] == "esp-fontanero"
    assert estado["canal_aviso"] == "correo"  # regla por defecto para urgencia alta
    incidencias = await repo.listar_incidencias()
    assert len(incidencias) == 1 and incidencias[0].estado == "asignada"
    llamada = repo.llamadas[estado["llamada_id"]]
    assert llamada.configuracion_id == "cfg-mayor-12-v1"
    assert llamada.grafo == "estandar"
    assert "datos_validados" in estado["validaciones"]


async def test_finca_no_identificada_tras_tres_intentos(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(
        gestor, ["Calle Inventada 99", "Otra calle rara", "Plaza Inexistente"]
    )
    assert estado["nodo_actual"] == "finca_no_identificada"
    assert estado["esperando_usuario"] is True
    estado = await gestor.mensaje(estado["llamada_id"], "Quería avisar de una farola rota")
    assert estado["finalizada"] is True
    assert estado["resultado"] == "finca_no_identificada"
    assert repo.llamadas[estado["llamada_id"]].resultado == "finca_no_identificada"
    assert await repo.listar_incidencias() == []


async def test_rechazar_finca_candidata_cuenta_como_intento(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "no"])
    assert estado["nodo_actual"] == "identificar_finca"
    assert estado["intentos_finca"] == 1
    assert estado["finca_id"] is None


async def test_vivienda_no_identificada_respeta_intentos_de_la_finca(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    # Alameda 5 tiene intentos_vivienda=4 en su configuración
    estado = await _conversar(
        gestor, ["Avenida de la Alameda 5", "sí", "noveno Z", "noveno Z", "noveno Z"]
    )
    assert estado["nodo_actual"] == "identificar_vivienda"
    assert estado["intentos_vivienda"] == 3
    estado = await gestor.mensaje(estado["llamada_id"], "noveno Z")
    assert estado["nodo_actual"] == "vivienda_no_identificada"
    estado = await gestor.mensaje(estado["llamada_id"], "Se ha roto la puerta del garaje")
    assert estado["resultado"] == "vivienda_no_identificada"
    assert estado["destino_aviso"] == "alameda5@demo.test"
    assert await repo.listar_incidencias() == []


async def test_vivienda_con_portal(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Alameda 5", "sí", "portal 2, segundo C"])
    assert estado["vivienda_id"] == "finca-alameda-5-2-2C"
    assert estado["nodo_actual"] == "recoger_solicitud"
    # Sin preguntar el nombre: se confirma la vivienda y se pasa directamente al problema
    assert estado["mensajes_bot"][-1] == "Perfecto, portal 2 2º C. Cuénteme, ¿qué ocurre?"


async def test_rechazar_resumen_vuelve_a_recoger_solicitud(gestor: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "1 A", "No hay luz en la escalera", "no"]
    )
    assert estado["nodo_actual"] == "recoger_solicitud"
    assert estado["confirmado"] is False
    estado = await gestor.mensaje(estado["llamada_id"], "El ascensor está parado con gente dentro")
    assert estado["categoria"] == "ascensor"
    assert estado["nodo_actual"] == "confirmar_resumen"


async def test_no_clasificable_crea_incidencia_pendiente(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "2 B", "He visto un ovni en la azotea", "sí"]
    )
    # Nosotros sustituimos al administrador: aunque no se clasifique, la incidencia se crea
    # y queda pendiente para él
    assert estado["finalizada"] is True
    assert estado["resultado"] == "incidencia_creada"
    assert "sin_categoria->pendiente_administrador" in estado["validaciones"]
    assert estado["especialista_id"] is None
    incidencias = await repo.listar_incidencias()
    assert len(incidencias) == 1 and incidencias[0].estado == "pendiente"
    assert "administrador la revisará" in estado["mensajes_bot"][-1]


async def test_categoria_sin_especialista_queda_pendiente_del_administrador(
    gestor: GestorLlamadas,
) -> None:
    # "limpieza" está en el catálogo pero el administrador demo no tiene especialista asignado
    estado = await _conversar(
        gestor,
        [
            "Calle Mayor 12",
            "sí",
            "2 B",
            "La basura del portal lleva días sin recogerse",
            "sí",
        ],
    )
    assert estado["resultado"] == "incidencia_creada"
    assert "sin_especialista->pendiente_administrador" in estado["validaciones"]
    assert estado["destino_aviso"] == "mayor12@demo.test"


async def test_mensaje_tras_finalizar_no_cambia_estado(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "2 B", "He visto un ovni", "sí"])
    de_nuevo = await gestor.mensaje(estado["llamada_id"], "hola?")
    assert de_nuevo == estado


def test_registro_solo_tiene_grafo_estandar(deps) -> None:
    assert sorted(REGISTRO) == ["estandar"]
    assert obtener_grafo("estandar", deps) is not None


async def test_historial_contiene_turnos_de_bot_y_vecino(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí"])
    roles = [t["rol"] for t in estado["historial"]]
    assert roles == ["bot", "vecino", "bot", "vecino", "bot"]
    assert estado["historial"][0]["texto"].startswith("Hola")


async def test_el_resumen_nombra_la_categoria_como_se_dice(gestor: GestorLlamadas) -> None:
    """El código interno (`fontaneria`) no se lee en voz alta: el vecino oye «fontanería»."""
    estado = await _conversar(
        gestor,
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
        ],
    )
    resumen = " ".join(estado["mensajes_bot"])
    assert "fontanería" in resumen and "fontaneria" not in resumen


async def test_el_cierre_respeta_las_mayusculas_del_profesional(gestor: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor,
        [
            "Vivo en la calle Mayor número 12",
            "sí",
            "tercero B",
            "Hay una fuga de agua en el baño y está inundando el suelo",
            "sí, correcto",
        ],
    )
    cierre = " ".join(estado["mensajes_bot"])
    assert "pérez" not in cierre and "Pérez" in cierre
