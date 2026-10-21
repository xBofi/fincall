"""El vecino habla a su manera; la máquina de estados se adapta sin perder el control."""

from __future__ import annotations

from typing import Any

from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas


async def _conversar(gestor: GestorLlamadas, turnos: list[str]) -> dict:
    estado = await gestor.iniciar()
    for texto in turnos:
        estado = await gestor.mensaje(estado["llamada_id"], texto)
    return estado


async def test_todo_en_una_frase_salta_las_preguntas_ya_respondidas(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(
        gestor,
        [
            "Hola, llamo desde Avenida de la Alameda 5, portal 2, tercero A. Soy Carmen "
            "y hay una fuga de agua en el baño",
            "sí",
        ],
    )
    # Tras confirmar la finca ya sabe vivienda, nombre y problema: va directo al resumen
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert estado["vivienda_id"] == "finca-alameda-5-2-3A"
    assert estado["nombre_declarado"] == "Carmen"
    assert estado["categoria"] == "fontaneria"
    preguntas = [h["texto"] for h in estado["historial"] if h["rol"] == "bot"]
    assert not any("¿Cuál es su vivienda?" in p for p in preguntas)
    assert not any("Dígame su nombre" in p for p in preguntas)
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert estado["resultado"] == "incidencia_creada"
    assert len(await repo.listar_incidencias()) == 1


async def test_datos_sueltos_se_acumulan_y_se_usan_despues(gestor: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "Cuarto A. Me llamo Pedro y no hay luz en la escalera"]
    )
    assert estado["vivienda_id"] == "finca-mayor-12-u-4A"
    assert estado["nombre_declarado"] == "Pedro"
    assert estado["solicitud"]
    assert estado["nodo_actual"] == "confirmar_resumen"


async def test_pedir_repetir_repite_la_pregunta(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "¿Cómo? No le he oído bien"])
    assert estado["nodo_actual"] == "identificar_vivienda"
    assert "le repito." in estado["mensajes_bot"][-1]
    assert "vivienda" in estado["mensajes_bot"][-1]


async def test_pregunta_del_vecino_se_responde_y_se_retoma(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["¿Esta llamada se está grabando?"])
    assert estado["nodo_actual"] == "identificar_finca"
    assert "se registra" in estado["mensajes_bot"][-1]
    assert "finca" in estado["mensajes_bot"][-1]
    assert estado["intentos_finca"] == 0  # no cuenta como intento fallido


async def test_fuera_de_ambito_explica_y_retoma(gestor: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "2 B", "¿Cuánto pago de cuota al mes?"]
    )
    assert estado["nodo_actual"] == "recoger_solicitud"
    assert "solo puedo gestionar incidencias" in estado["mensajes_bot"][-1].lower()


async def test_pedir_una_persona_deriva_al_administrador(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "Quiero hablar con una persona"])
    assert estado["finalizada"] is True
    assert estado["resultado"] == "derivada_administrador"
    assert estado["destino_aviso"] == "mayor12@demo.test"
    assert repo.llamadas[estado["llamada_id"]].resultado == "derivada_administrador"


async def test_despedida_cierra_la_llamada(
    gestor: GestorLlamadas, repo: RepositorioMemoria
) -> None:
    estado = await _conversar(gestor, ["Adiós, ya llamaré en otro momento"])
    assert estado["finalizada"] is True
    assert estado["resultado"] == "abandonada"
    assert await repo.listar_incidencias() == []


async def test_rechazar_candidata_con_otra_direccion_no_gasta_intento(
    gestor: GestorLlamadas,
) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "No, no, la avenida de la Alameda 5"])
    assert estado["finca_candidata_id"] == "finca-alameda-5"
    assert estado["intentos_finca"] == 0
    assert "Alameda 5" in estado["mensajes_bot"][-1]


async def test_correccion_en_el_resumen_reclasifica(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "3 B", "No hay luz en la escalera"])
    assert estado["categoria"] == "electricidad"
    estado = await gestor.mensaje(
        estado["llamada_id"], "No, me he equivocado, es una fuga de agua en el baño"
    )
    assert estado["categoria"] == "fontaneria"
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert estado["mensajes_bot"][0] == "De acuerdo, lo corrijo."


async def test_correccion_de_vivienda_en_el_resumen(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "3 B", "No hay luz en la escalera"])
    estado = await gestor.mensaje(estado["llamada_id"], "No, antes le he dicho 3 B pero es 3 A")
    assert estado["vivienda_id"] == "finca-mayor-12-u-3A"
    assert estado["nodo_actual"] == "confirmar_resumen"


async def test_turnos_deterministas_no_llaman_al_llm(gestor: GestorLlamadas, llm: Any) -> None:
    await _conversar(gestor, ["Calle Mayor 12", "sí", "3 B"])
    # dirección corta, sí/no y vivienda por regex: cero llamadas al modelo
    assert llm.llamadas == []
    agregados = gestor.metricas.agregados()
    assert agregados["turnos"] == 4
    assert agregados["servidor_ms_sin_llm"]["n"] == 4


async def test_metricas_registran_llamadas_llm_y_tiempos(gestor: GestorLlamadas, llm: Any) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "3 B", "No hay luz en la escalera"])
    turnos = gestor.metricas.turnos(estado["llamada_id"])
    ultimo = turnos[-1]
    assert ultimo.llm_llamadas == 1  # interpretación fusionada con clasificación
    assert ultimo.origen_interpretacion == "llm"
    assert ultimo.servidor_ms is not None
    assert estado["tiempos_turno"]["servidor_ms"] >= 0
    completado = gestor.metricas.completar_cliente(
        estado["llamada_id"], ultimo.turno, {"respuesta_ms": 1100, "vad_silencio_ms": 500}
    )
    assert completado is not None and completado.calificacion == "objetivo"
    assert gestor.metricas.agregados()["calificaciones"]["objetivo"] == 1


async def test_direccion_irreconocible_no_deriva_a_humano(gestor: GestorLlamadas, llm: Any) -> None:
    # El modelo se equivoca y marca "humano" ante una transcripción sin sentido
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {"intencion": "humano"}  # type: ignore[method-assign]
    estado = await _conversar(gestor, ["Estoy en la finca Carregüerdor del Saford, Doctor Mood"])
    llm._interpretar = original  # type: ignore[method-assign]
    assert estado["finalizada"] is False
    assert estado["nodo_actual"] == "identificar_finca"
    assert estado["intentos_finca"] == 1
    assert "repetirme la calle" in estado["mensajes_bot"][-1]


async def test_vivienda_inexistente_explica_la_estructura_de_la_finca(
    gestor: GestorLlamadas,
) -> None:
    # El piso existe: solo se ofrecen sus puertas
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "piso 1, puerta 2"])
    assert estado["nodo_actual"] == "identificar_vivienda"
    assert "En el 1º las puertas son A o B" in estado["mensajes_bot"][-1]
    estado = await gestor.mensaje(estado["llamada_id"], "piso 1 puerta B")
    assert estado["vivienda_id"] == "finca-mayor-12-u-1B"
    # Tampoco existe el piso: se describe la finca entera
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "piso 9, puerta 2"])
    assert "pisos del 1 al 4" in estado["mensajes_bot"][-1]
    assert "puertas A o B" in estado["mensajes_bot"][-1]


async def test_saludo_no_cuenta_como_intento_de_finca(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Hola, buenas."])
    assert estado["intentos_finca"] == 0
    assert estado["mensajes_bot"][-1].startswith("Hola, buenas.")
    estado = await gestor.mensaje(estado["llamada_id"], "Avenida de la Alameda 5")
    assert estado["finca_candidata_id"] == "finca-alameda-5"


async def test_piso_y_puerta_en_varios_portales_pregunta_el_portal(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Alameda 5", "sí", "El tercero A es mi vivienda"])
    assert estado["nodo_actual"] == "identificar_vivienda"
    assert estado["intentos_vivienda"] == 0
    assert "¿En qué portal vive, 1 o 2?" in estado["mensajes_bot"][-1]
    estado = await gestor.mensaje(estado["llamada_id"], "portal 1")
    assert estado["vivienda_id"] == "finca-alameda-5-1-3A"


async def test_resumen_incluye_nombre_y_permite_corregirlo(gestor: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "3 B", "Soy Reubofibra y no hay luz en la escalera"]
    )
    assert "Le resumo: Reubofibra, " in estado["mensajes_bot"][-1]
    assert "3º B" not in estado["mensajes_bot"][-1]
    estado = await gestor.mensaje(estado["llamada_id"], "No, me llamo Andreu Bofi")
    assert estado["nombre_declarado"] == "Andreu Bofi"
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert "Andreu Bofi" in estado["mensajes_bot"][-1]


async def test_cierre_explica_el_siguiente_paso(gestor: GestorLlamadas) -> None:
    estado = await gestor.iniciar(telefono_llamante="+34600000000")
    for t in ["Calle Mayor 12", "sí", "3 B", "Gotea el grifo del patio", "sí"]:
        estado = await gestor.mensaje(estado["llamada_id"], t)
    final = estado["mensajes_bot"][-1]
    assert "Fontanería Pérez" in final
    assert "le llamará a este mismo número" in final
    assert "no puedo darle hora" in final.lower() or "no puedo confirmarle la hora" in final.lower()
    assert "veinticuatro horas" in final


async def test_vivienda_con_muletillas_usa_el_regex_y_no_inventa_portal(
    gestor: GestorLlamadas, llm: Any
) -> None:
    original = llm._interpretar
    # El modelo se inventa un portal en una finca sin portales
    llm._interpretar = lambda usuario, esquema: {
        "portal": "a",
        "piso": "1",
        "puerta": "A",
        "confirma": True,
    }  # type: ignore[method-assign]
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "Ah, vale, perdón. Pues sí, piso 1, puerta A."]
    )
    llm._interpretar = original  # type: ignore[method-assign]
    assert estado["vivienda_id"] == "finca-mayor-12-u-1A"


async def test_comentario_sobre_el_bot_no_es_una_correccion(
    gestor: GestorLlamadas, llm: Any
) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "1 A", "Hay que pintar las paredes del corredor"]
    )
    # El resumen dice lo entendido, no la categoría interna (Andreu, 2026-10-21)
    assert "pintar las paredes" in estado["mensajes_bot"][-1]
    assert "no he podido" not in estado["mensajes_bot"][-1].lower()
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {
        "problema": "has podido clasificarlo con urgencia baja"
    }  # type: ignore[method-assign]
    estado = await gestor.mensaje(
        estado["llamada_id"], "Dirás que has podido clasificarlo con una urgencia baja."
    )
    llm._interpretar = original  # type: ignore[method-assign]
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert "Responda sí o no" in estado["mensajes_bot"][-1]
    assert estado["solicitud"].startswith("Hay que pintar")


async def test_si_transcrito_como_letra_confirma(gestor: GestorLlamadas, llm: Any) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "S."])
    assert estado["finca_id"] == "finca-mayor-12"
    assert llm.llamadas == []


async def test_muletilla_de_espera_no_gasta_intento(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "Ah, vale, espéreme."])
    assert estado["nodo_actual"] == "identificar_vivienda"
    assert estado["intentos_vivienda"] == 0
    assert "sin prisa" in estado["mensajes_bot"][-1]
    estado = await gestor.mensaje(estado["llamada_id"], "piso 1, puerta A")
    assert estado["vivienda_id"] == "finca-mayor-12-u-1A"


async def test_datos_parciales_de_vivienda_se_acumulan(gestor: GestorLlamadas, llm: Any) -> None:
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {
        "piso": "3"
    }  # el STT perdió la puerta  # type: ignore[method-assign]
    estado = await _conversar(gestor, ["Alameda 5", "sí", "El tercero es mi vivienda"])
    assert estado["intentos_vivienda"] == 0
    assert "¿Y la puerta?" in estado["mensajes_bot"][-1]
    llm._interpretar = lambda usuario, esquema: {"portal": "1"}  # type: ignore[method-assign]
    estado = await gestor.mensaje(estado["llamada_id"], "Portal 1")
    assert "¿Y la puerta?" in estado["mensajes_bot"][-1]
    llm._interpretar = original  # type: ignore[method-assign]
    estado = await gestor.mensaje(estado["llamada_id"], "puerta A")
    assert estado["vivienda_id"] == "finca-alameda-5-1-3A"


async def test_afirmacion_con_ruido_del_stt(gestor: GestorLlamadas, llm: Any) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "Ese, eso es."])
    assert estado["finca_id"] == "finca-mayor-12"
    assert llm.llamadas == []


def test_marcadores_vacios_del_modelo_no_son_datos() -> None:
    from building_call.llm.interpretacion import normalizar_interpretacion

    i = normalizar_interpretacion({"nombre": "No especificado", "piso": "3", "puerta": "null"})
    assert i.nombre is None and i.puerta is None and i.piso == "3"


async def test_faq_sin_signos_de_interrogacion(gestor: GestorLlamadas, llm: Any) -> None:
    estado = await _conversar(gestor, ["Esta llamada se está grabando."])
    assert "se registra" in estado["mensajes_bot"][-1]
    assert estado["intentos_finca"] == 0


async def test_pregunta_de_detalle_antes_del_resumen(gestor_detalle: GestorLlamadas) -> None:
    gestor = gestor_detalle
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "1 A", "No funciona la luz del pasillo del segundo"]
    )
    assert estado["nodo_actual"] == "pedir_detalle"
    assert "electricista" in estado["mensajes_bot"][-1]
    estado = await gestor.mensaje(estado["llamada_id"], "Son todas las del pasillo, no encienden")
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert "Detalle: Son todas las del pasillo" in estado["solicitud"]
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    assert estado["resultado"] == "incidencia_creada"


async def test_descripcion_larga_no_pide_detalle(gestor_detalle: GestorLlamadas) -> None:
    gestor = gestor_detalle
    larga = (
        "No funciona ninguna de las luces del pasillo del segundo piso desde ayer por la noche, "
        "no encienden aunque le des al interruptor y el resto del edificio tiene luz"
    )
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", larga])
    assert estado["nodo_actual"] == "confirmar_resumen"


async def test_repetir_devuelve_el_mensaje_completo_y_hola_tras_mensaje_no_oido(
    gestor: GestorLlamadas, llm: Any
) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", "Gotea el grifo"])
    resumen = estado["mensajes_bot"][-1]
    assert resumen.startswith("Le resumo")
    # El navegador informa de que el mensaje se cortó al 20 %
    await gestor.registrar_entrega(
        estado["llamada_id"], estado["turno"], "interrumpida", 0.2, "Le re"
    )
    n = len(llm.llamadas)
    estado = await gestor.mensaje(estado["llamada_id"], "¿Hola? ¿Estás ahí?")
    assert len(llm.llamadas) == n  # determinista
    assert estado["mensajes_bot"][-1] == f"Perdone, le repito. {resumen}"
    assert estado["nodo_actual"] == "confirmar_resumen"
    estado = await gestor.mensaje(estado["llamada_id"], "¿Me lo repites?")
    assert resumen in estado["mensajes_bot"][-1]


async def test_el_nombre_no_se_pregunta(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A"])
    assert estado["nodo_actual"] == "recoger_solicitud"
    assert not any("nombre" in m for m in estado["mensajes_bot"])
    estado = await gestor.mensaje(estado["llamada_id"], "¿Para qué necesita mi nombre?")
    assert "No necesito su nombre" in estado["mensajes_bot"][-1]
    assert estado["nombre_declarado"] is None
    assert estado["nodo_actual"] == "recoger_solicitud"


async def test_nombre_dicho_por_su_cuenta_se_guarda(gestor: GestorLlamadas) -> None:
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A, soy Ana"])
    assert estado["nombre_declarado"] == "Ana"
    assert estado["mensajes_bot"][-1].startswith("Gracias, Ana. Cuénteme")


async def test_frase_cortada_al_dar_vivienda_no_se_toma_por_averia(
    gestor: GestorLlamadas, llm: Any
) -> None:
    # Llamada real 2026-10-11: el STT oyó "Primero abre la" y el modelo lo devolvió como problema
    # no clasificable; el bot se saltó "¿qué ocurre?" y pidió detalles de una avería inexistente.
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {  # type: ignore[method-assign]
        "problema": "Primero abre la",
        "categoria": "no_clasificable",
    }
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "Primero abre la"])
    llm._interpretar = original  # type: ignore[method-assign]
    estado = await gestor.mensaje(estado["llamada_id"], "Piso 1, puerta A.")
    assert estado["vivienda_id"] == "finca-mayor-12-u-1A"
    assert estado["nodo_actual"] == "recoger_solicitud"
    assert not estado.get("solicitud")


async def test_puerta_inexistente_con_piso_valido_pregunta_solo_la_puerta(
    gestor: GestorLlamadas, llm: Any
) -> None:
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {"piso": "1", "puerta": "1"}  # type: ignore[method-assign]
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "La puerta primero."])
    llm._interpretar = original  # type: ignore[method-assign]
    ultimo = estado["mensajes_bot"][-1]
    assert "No encuentro esa vivienda" not in ultimo
    assert "A o B" in ultimo
    assert estado["pendientes"].get("piso") == "1"
    estado = await gestor.mensaje(estado["llamada_id"], "puerta A")
    assert estado["vivienda_id"] == "finca-mayor-12-u-1A"


async def test_repetir_dos_veces_no_acumula_la_coletilla(gestor: GestorLlamadas) -> None:
    estado = await _conversar(
        gestor, ["Calle Mayor 12", "sí", "¿Cómo? No le he oído bien", "¿Cómo? No le he oído bien"]
    )
    assert estado["mensajes_bot"][-1].count("le repito") == 1
    assert "vivienda" in estado["mensajes_bot"][-1]


async def test_si_suelto_ante_la_direccion_no_llama_al_llm(
    gestor: GestorLlamadas, llm: Any
) -> None:
    # Con Gemini, "Sí." ante "¿en qué finca?" tardaba 1,4 s en decir que no era una dirección
    estado = await _conversar(gestor, ["Sí."])
    assert llm.llamadas == []
    assert estado["nodo_actual"] == "identificar_finca"
    assert "calle y el número" in estado["mensajes_bot"][-1]


async def test_saludo_avisa_de_la_ia_y_de_la_grabacion(gestor: GestorLlamadas) -> None:
    estado = await gestor.iniciar()
    saludo = estado["mensajes_bot"][0].lower()
    assert "inteligencia artificial" in saludo
    assert "grabada" in saludo
    # Repetir la pregunta no vuelve a leer el aviso entero
    assert estado["ultima_pregunta"].startswith("¿En qué finca")


# ------------------------------------------------- enrutamiento: las palabras sueltas no deciden
# Detectores por subcadena secuestraban incidencias reales (2026-10-21): "cuando" o "tarda" se
# respondían como pregunta frecuente, "repite" como petición de repetir y "espere" como muletilla.


async def test_incidencias_con_palabras_de_faq_se_registran_como_incidencia(
    gestor: GestorLlamadas,
) -> None:
    casos = {
        "El ascensor tarda mucho en venir": "ascensor",
        "La luz de la escalera se apaga cuando subo": "electricidad",
        "Pierde agua el registro del portal": "fontaneria",
    }
    for frase, categoria in casos.items():
        estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", frase])
        assert estado["nodo_actual"] == "confirmar_resumen", frase
        assert estado["solicitud"] == frase
        assert estado["categoria"] == categoria


async def test_repite_dentro_de_una_averia_no_es_pedir_que_repita(gestor: GestorLlamadas) -> None:
    frase = "Se repite el ruido de la caldera"
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", frase])
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert estado["solicitud"] == frase


async def test_espere_dentro_de_una_averia_no_es_muletilla(gestor: GestorLlamadas) -> None:
    frase = "Espere, que el ascensor no va"
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", frase])
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert estado["categoria"] == "ascensor"


def test_detectores_solo_aceptan_la_frase_con_muletillas() -> None:
    from building_call.graph.nodos import _es_relleno, _es_saludo, _no_lo_oyo

    assert _no_lo_oyo("¿Cómo? No le he oído bien", {})
    assert _no_lo_oyo("Perdone, ¿me lo repite?", {})
    assert not _no_lo_oyo("Se repite el ruido", {})
    assert not _no_lo_oyo("Como dice el portero, no hay luz", {})
    assert _es_relleno("Ah, vale, espéreme.")
    assert _es_relleno("Un momento, por favor")
    assert not _es_relleno("Espere que el ascensor no va")
    assert _es_saludo("Hola, buenas tardes")
    assert not _es_saludo("Hola, el ascensor no va")


async def test_faq_la_interpreta_el_modelo_y_se_responde(gestor: GestorLlamadas, llm: Any) -> None:
    estado = await _conversar(gestor, ["¿Esta llamada se está grabando?"])
    assert "se registra" in estado["mensajes_bot"][-1]
    assert estado["intentos_finca"] == 0
    assert len(llm.llamadas) == 1


async def test_averia_sin_elemento_pide_que_falla_y_reclasifica(
    gestor_detalle: GestorLlamadas,
) -> None:
    gestor = gestor_detalle
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "1 A", "Está parado"])
    assert estado["categoria"] == "no_clasificable"
    assert estado["nodo_actual"] == "pedir_detalle"
    assert "qué es lo que" in estado["mensajes_bot"][-1].lower()
    estado = await gestor.mensaje(
        estado["llamada_id"], "El ascensor, entre el segundo y el tercero"
    )
    assert estado["nodo_actual"] == "confirmar_resumen"
    assert estado["categoria"] == "ascensor"


async def test_frase_cortada_sin_categoria_tampoco_se_toma_por_averia(
    gestor: GestorLlamadas, llm: Any
) -> None:
    # Si el modelo devuelve el problema sin categoría, el hueco era el mismo
    original = llm._interpretar
    llm._interpretar = lambda usuario, esquema: {"problema": "Primero abre la"}  # type: ignore[method-assign]
    estado = await _conversar(gestor, ["Calle Mayor 12", "sí", "Primero abre la"])
    llm._interpretar = original  # type: ignore[method-assign]
    estado = await gestor.mensaje(estado["llamada_id"], "Piso 1, puerta A.")
    assert estado["nodo_actual"] == "recoger_solicitud"
    assert not estado.get("solicitud")
