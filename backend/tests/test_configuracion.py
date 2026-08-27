import pytest
from pydantic import ValidationError

from building_call.domain.configuracion import (
    DEFAULTS,
    ConfiguracionParcial,
    ReglaAviso,
    resolver,
)


def test_sin_capas_devuelve_defaults() -> None:
    assert resolver() == DEFAULTS


def test_finca_sobrescribe_administrador_y_defaults() -> None:
    adm = ConfiguracionParcial(intentos_vivienda=5, especialista_por_categoria={"fontaneria": "f1"})
    finca = ConfiguracionParcial(intentos_vivienda=2, grafo="con_horario_nocturno")
    cfg = resolver(adm, finca)
    assert cfg.intentos_vivienda == 2
    assert cfg.grafo == "con_horario_nocturno"
    assert cfg.especialista_por_categoria == {"fontaneria": "f1"}
    assert cfg.intentos_finca == DEFAULTS.intentos_finca


def test_campo_ausente_hereda_default_global() -> None:
    cfg = resolver(ConfiguracionParcial(), ConfiguracionParcial())
    assert cfg.catalogo_categorias == DEFAULTS.catalogo_categorias


def test_especialista_fuera_de_catalogo_es_error() -> None:
    with pytest.raises(ValidationError, match="fuera del catálogo"):
        resolver(ConfiguracionParcial(especialista_por_categoria={"jardineria": "j1"}))


def test_emergencia_se_trata_como_alta_hasta_activarse() -> None:
    assert DEFAULTS.urgencia_efectiva("emergencia") == "alta"
    activa = resolver(ConfiguracionParcial(emergencias_activas=True))
    assert activa.urgencia_efectiva("emergencia") == "emergencia"


def test_regla_para_usa_urgencia_efectiva() -> None:
    cfg = resolver(
        ConfiguracionParcial(
            reglas_aviso=[ReglaAviso(urgencia="alta", canal="llamada", destino="administrador")]
        )
    )
    regla = cfg.regla_para("emergencia")
    assert regla is not None and regla.canal == "llamada"


def test_similitud_direccion_con_numeros_en_letra() -> None:
    from building_call.graph.nodos import _similitud_direccion

    assert _similitud_direccion("Vivo en la calle Mayor doce", "Calle Mayor 12") >= 0.6
    assert _similitud_direccion("avenida alameda treinta y dos", "Avenida de la Alameda 32") >= 0.6
    assert _similitud_direccion("Plaza Inexistente 99", "Calle Mayor 12") < 0.6


def test_vivienda_regex_y_normalizacion_de_numeros_en_letra() -> None:
    from building_call.graph.nodos import _extraer_vivienda_regex, _normalizar_portal

    assert _normalizar_portal("dos") == "2"
    assert _extraer_vivienda_regex("portal dos, segundo C") == ("2", "2", "C")
    assert _extraer_vivienda_regex("en el tercero B") == (None, "3", "B")
    assert _extraer_vivienda_regex("3º A") == (None, "3", "A")
