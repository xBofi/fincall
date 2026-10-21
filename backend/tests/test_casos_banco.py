"""Calidad marca turnos reales del vecino como casos del banco de pruebas.

Cada fallo real que se convierte en un caso del banco (`evals/gold_*.jsonl`) es lo que más ha
mejorado al asistente: la revisión de calidad lo hace desde el expediente de la llamada.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SECRETO_DE_PRUEBA, sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.casos_banco import quitar_telefonos, seudonimizar
from building_call.domain.operaciones import Rol
from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.settings import settings as settings_globales


@pytest.fixture
async def cliente(deps: Dependencias, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "audio_dir", str(tmp_path / "audio"))
    monkeypatch.setattr(settings_globales, "sesion_secreto", SECRETO_DE_PRUEBA)
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _conversar(cliente: AsyncClient, repo: RepositorioMemoria, turnos: list[str]) -> str:
    cliente.cookies.set("bc_sesion", sesion_de_prueba(repo))
    lid = (await cliente.post("/api/llamadas", json={"telefono_llamante": "+34600111222"})).json()[
        "llamada_id"
    ]
    for t in turnos:
        await cliente.post(f"/api/llamadas/{lid}/mensajes", json={"texto": t})
    return lid


def _como(cliente: AsyncClient, repo: RepositorioMemoria, rol: Rol) -> None:
    cliente.cookies.set("bc_sesion", sesion_de_prueba(repo, str(rol)))


CASO = {
    "turno": 1,
    "esperado": {"intencion": "continuar", "direccion": "mayor 12"},
    "nota": "No pilló la calle",
}


@pytest.mark.parametrize("rol", [Rol.calidad, Rol.administrador, Rol.owner])
async def test_los_revisores_marcan_un_turno_como_caso(
    cliente: AsyncClient, repo: RepositorioMemoria, rol: Rol
) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12", "sí"])
    _como(cliente, repo, rol)

    r = await cliente.post(f"/api/operaciones/llamadas/{lid}/casos", json=CASO)
    assert r.status_code == 201, r.text
    caso = r.json()
    assert caso["turno"] == 1 and caso["banco"] == "interpretacion"
    assert caso["entrada"] == "Calle Mayor 12"
    assert caso["nodo"] == "identificar_finca"
    # La pregunta es lo último que dijo el bot antes de que el vecino hablara
    detalle = (await cliente.get(f"/api/operaciones/llamadas/{lid}")).json()
    assert caso["pregunta"] == detalle["turnos"][0]["asistente"][-1]
    assert caso["autor_id"] == f"usr-{rol}" and caso["creado_en"]
    assert caso["esperado"] == CASO["esperado"] and caso["nota"] == "No pilló la calle"
    # Nunca guarda el teléfono del vecino
    assert "600111222" not in str(caso)

    # Aparece en el expediente y en el listado general
    assert [c["id"] for c in detalle["casos"]] == [caso["id"]]
    todos = (await cliente.get("/api/operaciones/casos")).json()
    assert [c["id"] for c in todos] == [caso["id"]]


async def test_sin_sesion_no_se_marca(cliente: AsyncClient, repo: RepositorioMemoria) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12"])
    cliente.cookies.clear()
    assert (
        await cliente.post(f"/api/operaciones/llamadas/{lid}/casos", json=CASO)
    ).status_code == 401
    assert (await cliente.get("/api/operaciones/casos")).status_code == 401


async def test_marcar_queda_en_la_auditoria_y_desmarcar_tambien(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12"])
    _como(cliente, repo, Rol.calidad)
    caso = (await cliente.post(f"/api/operaciones/llamadas/{lid}/casos", json=CASO)).json()
    _como(cliente, repo, Rol.owner)
    eventos = (await cliente.get("/api/operaciones/auditoria")).json()
    marcado = next(e for e in eventos if e["accion"] == "marcar_caso_banco")
    assert marcado["usuario_id"] == "usr-calidad"
    assert marcado["recurso"] == f"llamada:{lid}"
    assert marcado["detalle"]["turno"] == 1 and marcado["detalle"]["caso_id"] == caso["id"]

    assert (await cliente.delete(f"/api/operaciones/casos/{caso['id']}")).status_code == 204
    assert (await cliente.get("/api/operaciones/casos")).json() == []
    eventos = (await cliente.get("/api/operaciones/auditoria")).json()
    assert any(e["accion"] == "desmarcar_caso_banco" for e in eventos)
    assert (await cliente.delete(f"/api/operaciones/casos/{caso['id']}")).status_code == 404


async def test_volver_a_marcar_el_mismo_turno_lo_corrige(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12"])
    _como(cliente, repo, Rol.calidad)
    primero = (await cliente.post(f"/api/operaciones/llamadas/{lid}/casos", json=CASO)).json()
    otro = {**CASO, "esperado": {"direccion": "mayor 12"}, "nota": "mejor así"}
    segundo = (await cliente.post(f"/api/operaciones/llamadas/{lid}/casos", json=otro)).json()
    assert segundo["id"] == primero["id"]
    todos = (await cliente.get("/api/operaciones/casos")).json()
    assert len(todos) == 1 and todos[0]["esperado"] == {"direccion": "mayor 12"}


@pytest.mark.parametrize(
    ("cuerpo", "estado"),
    [
        ({**CASO, "turno": 0}, 422),  # el saludo no tiene texto del vecino
        ({**CASO, "turno": 99}, 404),
        ({**CASO, "esperado": {}}, 422),
        ({**CASO, "esperado": {"intencion": "bailar"}}, 422),
        ({**CASO, "esperado": {"urgencia": "altisima"}}, 422),
        ({**CASO, "esperado": {"actuacion": ["reparacion", "magia"]}}, 422),
        ({**CASO, "esperado": {"confirma": "quizá"}}, 422),
        ({**CASO, "esperado": {"telefono": "600"}}, 422),
        ({**CASO, "banco": "clasificacion", "esperado": {"categoria": "fontaneria"}}, 422),
    ],
)
async def test_valida_lo_que_se_marca(
    cliente: AsyncClient, repo: RepositorioMemoria, cuerpo: dict, estado: int
) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12"])
    _como(cliente, repo, Rol.calidad)
    r = await cliente.post(f"/api/operaciones/llamadas/{lid}/casos", json=cuerpo)
    assert r.status_code == estado, r.text


async def test_llamada_inexistente(cliente: AsyncClient, repo: RepositorioMemoria) -> None:
    _como(cliente, repo, Rol.calidad)
    r = await cliente.post("/api/operaciones/llamadas/llam-nada/casos", json=CASO)
    assert r.status_code == 404


async def test_caso_de_clasificacion_y_comodines(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12", "sí", "3 B", "Gotea el grifo"])
    _como(cliente, repo, Rol.calidad)
    r = await cliente.post(
        f"/api/operaciones/llamadas/{lid}/casos",
        json={
            "turno": 4,
            "banco": "clasificacion",
            "esperado": {"categoria": "fontaneria", "urgencia": ["baja", "media"]},
        },
    )
    assert r.status_code == 201, r.text
    r = await cliente.post(
        f"/api/operaciones/llamadas/{lid}/casos",
        json={
            "turno": 4,
            "esperado": {"problema": "*", "categoria": "fontaneria", "equipo": None},
            "oido": "Cuénteme",
        },
    )
    assert r.status_code == 201, r.text
    caso = r.json()
    assert caso["catalogo"] is True and caso["oido"] == "Cuénteme"
    assert len((await cliente.get(f"/api/operaciones/llamadas/{lid}/casos")).json()) == 2


async def test_el_nombre_del_vecino_se_seudonimiza(
    cliente: AsyncClient, repo: RepositorioMemoria
) -> None:
    lid = await _conversar(cliente, repo, ["Calle Mayor 12", "sí", "3 B", "Soy Remedios Quintana"])
    repo.llamadas[lid] = repo.llamadas[lid].model_copy(
        update={"nombre_declarado": "Remedios Quintana"}
    )
    _como(cliente, repo, Rol.calidad)
    caso = (
        await cliente.post(
            f"/api/operaciones/llamadas/{lid}/casos",
            json={"turno": 4, "esperado": {"nombre": "Remedios"}},
        )
    ).json()
    assert "Remedios" not in str(caso) and "Quintana" not in str(caso)
    assert caso["entrada"] == "Soy Carmen Ruiz"
    assert caso["esperado"] == {"nombre": "Carmen"}


def test_seudonimizar_y_quitar_telefonos() -> None:
    assert seudonimizar("hola, soy remedios", "Remedios") == "hola, soy Carmen"
    assert seudonimizar("Soy Ana", None) == "Soy Ana"
    assert seudonimizar("Soy Ana", "Ana López") == "Soy Carmen"
    assert quitar_telefonos("llamadme al 600 11 22 33 o +34 912345678") == (
        "llamadme al 600 000 000 o 600 000 000"
    )
    assert quitar_telefonos("tercero B del 12") == "tercero B del 12"
