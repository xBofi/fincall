"""Exportador de los casos marcados por calidad al banco de pruebas (`evals/gold_*.jsonl`)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

EVALS = Path(__file__).resolve().parents[1] / "evals"


def _modulo() -> ModuleType:
    spec = importlib.util.spec_from_file_location("exportar_casos", EVALS / "exportar_casos.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exportar_casos"] = mod
    spec.loader.exec_module(mod)
    return mod


exportar_casos = _modulo()


def _caso(**cambios: object) -> dict:
    base = {
        "id": "cb-1",
        "llamada_id": "llam-abc",
        "turno": 3,
        "banco": "interpretacion",
        "nodo": "recoger_solicitud",
        "pregunta": "Cuénteme, ¿qué ocurre?",
        "oido": None,
        "entrada": "El portero no se oye, llamadme al 600 11 22 33",
        "catalogo": True,
        "equipos": [["eq-1", "portero automático Fermax"]],
        "esperado": {"problema": "*", "equipo": "eq-1"},
        "nota": "Lo dijo Remedios, muy enfadada",
        "autor_id": "usr-calidad",
        "creado_en": "2026-10-22T10:00:00+00:00",
        "telefono_llamante": "+34600111222",
        "nombre_declarado": "Remedios",
    }
    return {**base, **cambios}


def _escribir(ruta: Path, lineas: list[dict]) -> None:
    ruta.write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lineas), encoding="utf-8"
    )


def test_formato_de_interpretacion_igual_que_el_banco() -> None:
    fichero, linea = exportar_casos.caso_a_linea(_caso())
    assert fichero == "gold_interpretacion.jsonl"
    # Mismo orden de claves que los casos escritos a mano; solo turno y pregunta del bot
    assert list(linea) == ["nodo", "pregunta", "entrada", "catalogo", "equipos", "esperado"]
    assert linea["entrada"] == "El portero no se oye, llamadme al 600 000 000"
    assert linea["equipos"] == [["eq-1", "portero automático Fermax"]]
    texto = json.dumps(linea, ensure_ascii=False)
    for privado in ("600111222", "Remedios", "llam-abc", "usr-calidad", "enfadada"):
        assert privado not in texto


def test_oido_y_sin_catalogo_ni_equipos() -> None:
    _, linea = exportar_casos.caso_a_linea(
        _caso(oido="Le resumo: gotea", catalogo=False, equipos=None, esperado={"confirma": True})
    )
    assert list(linea) == ["nodo", "pregunta", "oido", "entrada", "esperado"]


def test_formato_de_clasificacion() -> None:
    fichero, linea = exportar_casos.caso_a_linea(
        _caso(
            banco="clasificacion",
            entrada="Hay una fuga en el garaje",
            esperado={"categoria": "fontaneria", "urgencia": "alta"},
        )
    )
    assert fichero == "gold_clasificacion.jsonl"
    assert linea == {
        "solicitud": "Hay una fuga en el garaje",
        "categoria": "fontaneria",
        "urgencias": ["alta"],
    }


def test_no_duplica_casos_ya_presentes(tmp_path: Path) -> None:
    _escribir(
        tmp_path / "gold_interpretacion.jsonl",
        [
            {
                "nodo": "recoger_solicitud",
                "pregunta": "Cuénteme, ¿qué ocurre?",
                "entrada": "el portero no se oye,  llamadme al 600 000 000",
                "esperado": {"problema": "*"},
            }
        ],
    )
    _escribir(
        tmp_path / "gold_clasificacion.jsonl",
        [{"solicitud": "Gotea el grifo", "categoria": "fontaneria", "urgencias": ["baja"]}],
    )
    casos = [
        _caso(),  # ya está (misma frase, distinta capitalización y espacios)
        _caso(id="cb-2", entrada="Se ha ido la luz del portal"),
        _caso(id="cb-3", entrada="Se ha ido la luz del portal"),  # repetido en la tanda
        _caso(
            id="cb-4",
            banco="clasificacion",
            entrada="gotea el grifo",
            esperado={"categoria": "fontaneria", "urgencia": "baja"},
        ),
        _caso(
            id="cb-5",
            banco="clasificacion",
            entrada="El ascensor hace ruido",
            esperado={"categoria": "ascensor", "urgencia": ["baja", "media"]},
        ),
    ]
    resultado = exportar_casos.exportar(casos, tmp_path)
    assert resultado == {
        "gold_interpretacion.jsonl": 1,
        "gold_clasificacion.jsonl": 1,
        "omitidos": 3,
    }
    interp = (tmp_path / "gold_interpretacion.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(interp) == 2 and json.loads(interp[1])["entrada"] == "Se ha ido la luz del portal"
    clasif = (tmp_path / "gold_clasificacion.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(clasif[-1]) == {
        "solicitud": "El ascensor hace ruido",
        "categoria": "ascensor",
        "urgencias": ["baja", "media"],
    }
    # Idempotente: una segunda pasada no añade nada
    assert exportar_casos.exportar(casos, tmp_path)["omitidos"] == 5


def test_simulacion_no_escribe(tmp_path: Path) -> None:
    (tmp_path / "gold_interpretacion.jsonl").write_text("", encoding="utf-8")
    r = exportar_casos.exportar([_caso()], tmp_path, simular=True)
    assert r["gold_interpretacion.jsonl"] == 1
    assert (tmp_path / "gold_interpretacion.jsonl").read_text(encoding="utf-8") == ""


def test_main_lee_un_json_descargado_de_la_consola(tmp_path: Path) -> None:
    descarga = tmp_path / "casos.json"
    descarga.write_text(json.dumps([_caso()]), encoding="utf-8")
    exportar_casos.main(["--entrada", str(descarga), "--destino", str(tmp_path)])
    lineas = (tmp_path / "gold_interpretacion.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 1 and json.loads(lineas[0])["nodo"] == "recoger_solicitud"


def test_los_casos_exportados_los_entiende_comparar_modelos(tmp_path: Path) -> None:
    """Las claves que usa `evals/comparar_modelos.py` para reproducir el turno."""
    _, linea = exportar_casos.caso_a_linea(_caso(oido="Cuén"))
    for clave in ("nodo", "pregunta", "entrada", "esperado", "catalogo", "equipos", "oido"):
        assert clave in linea
    assert all(isinstance(e, list) and len(e) == 2 for e in linea["equipos"])
