"""LiveKit: el agente habla con la API con su secreto y la consola pide su token de sala."""

from __future__ import annotations

import pytest
from conftest import sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph.ledger import corte_por_palabras
from building_call.graph.nodos import Dependencias
from building_call.settings import settings
from building_call.voz.control import ControlLlamada

SECRETO = "secreto-del-agente-con-largo-suficiente-0123456789"


@pytest.fixture
def con_livekit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agente_secreto", SECRETO)
    monkeypatch.setattr(settings, "livekit_url", "ws://localhost:7880")
    monkeypatch.setattr(settings, "livekit_api_key", "devkey")
    monkeypatch.setattr(settings, "livekit_api_secret", "secret-de-desarrollo-con-largo-suficiente")


@pytest.fixture
async def app(deps: Dependencias, con_livekit: None):  # noqa: ARG001
    return crear_app(deps, fabrica_streaming=None)


async def test_el_agente_conversa_con_su_secreto(app) -> None:  # type: ignore[no-untyped-def]
    cabeceras = {"Authorization": f"Bearer {SECRETO}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/llamadas", json={}, headers=cabeceras)
        assert r.status_code == 201
        vista = r.json()
        assert vista["piezas"] and vista["piezas"][-1]["importancia"] == "pregunta"
        r = await c.post(
            f"/api/llamadas/{vista['llamada_id']}/mensajes",
            json={"texto": "Calle Mayor 12"},
            headers=cabeceras,
        )
        assert r.status_code == 200 and r.json()["turno"] == 1


async def test_secreto_incorrecto_o_sin_configurar_no_entra(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/llamadas", json={}, headers={"Authorization": "Bearer otro"})
        assert r.status_code == 401
        monkeypatch.setattr(settings, "agente_secreto", "")
        r = await c.post("/api/llamadas", json={}, headers={"Authorization": "Bearer "})
        assert r.status_code == 401


async def test_la_consola_pide_token_de_sala(app, deps: Dependencias) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/livekit/token", json={"llamada_id": "llam-1"})
        assert r.status_code == 401
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"llamada_id": "llam-1"})
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["url"] == "ws://localhost:7880"
        assert cuerpo["sala"].startswith("consola-")
        from livekit import api

        claims = api.TokenVerifier("devkey", "secret-de-desarrollo-con-largo-suficiente").verify(
            cuerpo["token"]
        )
        assert claims.video.room == cuerpo["sala"] and claims.video.room_join
        despacho = claims.room_config.agents[0]
        assert despacho.agent_name == "fincall"
        assert '"llamada_id": "llam-1"' in despacho.metadata


async def test_token_sin_livekit_configurado(
    deps: Dependencias, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "livekit_api_key", "")
    app = crear_app(deps, fabrica_streaming=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"llamada_id": "llam-1"})
        assert r.status_code == 503


# ----------------------------------------------------------------- lo que oyó, por palabras


def test_corte_por_palabras_ignora_puntuacion_y_espacios() -> None:
    texto = "Le resumo: gotea el grifo. ¿Es correcto?"
    # LiveKit devuelve las palabras sincronizadas con el audio, no el texto exacto
    assert texto[: corte_por_palabras(texto, "Le resumo gotea")] == "Le resumo: gotea"
    assert corte_por_palabras(texto, "") == 0
    assert corte_por_palabras(texto, "Le resumo: gotea el grifo. ¿Es correcto?") == len(texto)


# ----------------------------------------------------------------- control de la llamada


def test_control_cuelga_tras_el_cierre_oido() -> None:
    control = ControlLlamada("llam-1")
    control.nuevo_turno({"turno": 4, "finalizada": True, "mensajes_bot": ["Hasta luego."]})
    assert control.al_terminar_de_hablar({"piezas": []}) == ("colgar", [])


def test_control_repite_el_aviso_cortado_antes_de_colgar() -> None:
    control = ControlLlamada("llam-1")
    control.nuevo_turno({"turno": 4, "finalizada": True, "mensajes_bot": ["..."]})
    entrega = {
        "piezas": [
            {
                "importancia": "aviso",
                "estado": "entregada",
                "texto": "He registrado su incidencia.",
            },
            {"importancia": "aviso", "estado": "no_oida", "texto": "Le llamará a este número."},
            {"importancia": "cortesia", "estado": "no_entregada", "texto": "Hasta luego."},
        ]
    }
    assert control.al_terminar_de_hablar(entrega) == (
        "repetir_y_colgar",
        ["Le llamará a este número."],
    )


def test_control_sigue_si_la_llamada_no_ha_terminado() -> None:
    control = ControlLlamada("llam-1")
    control.nuevo_turno({"turno": 1, "finalizada": False, "mensajes_bot": ["¿Es correcto?"]})
    assert control.al_terminar_de_hablar({"piezas": []}) == ("seguir", [])


async def test_el_agente_adelanta_la_interpretacion_con_el_parcial(app, deps: Dependencias) -> None:  # type: ignore[no-untyped-def]
    cabeceras = {"Authorization": f"Bearer {SECRETO}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        llamada_id = (await c.post("/api/llamadas", json={}, headers=cabeceras)).json()[
            "llamada_id"
        ]
        r = await c.post(
            f"/api/llamadas/{llamada_id}/parcial", json={"texto": "Vivo en la"}, headers=cabeceras
        )
        assert r.status_code == 202
        assert deps.especulaciones.get(llamada_id)


async def test_el_agente_pide_el_vocabulario_de_las_fincas(app) -> None:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/api/voz/vocabulario", headers={"Authorization": f"Bearer {SECRETO}"})
        assert r.status_code == 200
        assert "Calle Mayor 12" in " ".join(r.json())
