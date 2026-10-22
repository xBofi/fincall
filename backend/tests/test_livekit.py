"""LiveKit: el agente habla con la API con su secreto y la consola pide su token de sala."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import sesion_de_prueba
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph.ledger import corte_por_palabras
from building_call.graph.nodos import Dependencias
from building_call.settings import settings
from building_call.voz.control import ControlLlamada, solo_muletillas

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


async def test_el_despacho_usa_el_nombre_de_agente_configurado(
    app, deps: Dependencias, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Otro agente (pruebas, banco) puede convivir con el de la consola en el mismo LiveKit."""
    monkeypatch.setattr(settings, "livekit_agente", "fincall-pruebas")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
        r = await c.post("/api/livekit/token", json={"llamada_id": "llam-1"})
        from livekit import api

        claims = api.TokenVerifier("devkey", "secret-de-desarrollo-con-largo-suficiente").verify(
            r.json()["token"]
        )
        assert claims.room_config.agents[0].agent_name == "fincall-pruebas"


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


# ----------------------------------------------------------------- turnos partidos e interrumpidos


class _Msg:
    def __init__(self, id: str, role: str, texto: str) -> None:  # noqa: A002
        self.id, self.role, self.text_content = id, role, texto


class _ApiFalsa:
    """Registra el orden de las llamadas; `turno` tarda lo que se le diga."""

    def __init__(self, espera_s: float = 0.0) -> None:
        self.llamadas: list[tuple[str, Any]] = []
        self.espera_s = espera_s
        self.n = 0

    async def turno(self, llamada_id: str, texto: str) -> dict[str, Any]:
        self.llamadas.append(("turno", texto))
        await asyncio.sleep(self.espera_s)
        self.n += 1
        return {"turno": self.n, "finalizada": False, "mensajes_bot": [f"respuesta {self.n}"]}

    async def entrega(
        self, llamada_id: str, turno: int, interrumpida: bool, texto: str, ratio: float
    ) -> dict[str, Any]:
        self.llamadas.append(("entrega", (turno, interrumpida, texto)))
        return {}


async def test_control_junta_lo_que_el_vecino_dijo_y_aun_no_se_ha_enviado() -> None:
    """AssemblyAI parte "Sí, eso es" en dos finales si el vecino hace una pausa: lo que no se
    mandó a la API va en un solo turno, y lo ya mandado no se repite. Lo que se quedó esperando
    (LiveKit canceló la generación antes de enviarlo) sigue pendiente."""
    api = _ApiFalsa()
    control = ControlLlamada("llam-1")
    items = [_Msg("u1", "user", "Sí."), _Msg("a1", "assistant", "¿Es correcto?")]
    assert control.por_enviar(items) == "Sí."
    await control.enviar_turno(api, "Sí.")
    items += [_Msg("u2", "user", "Eso es.")]
    assert control.por_enviar(items) == "Eso es."  # no se llegó a enviar
    items += [_Msg("u3", "user", "Del techo.")]
    assert control.por_enviar(items) == "Eso es. Del techo."
    await control.enviar_turno(api, "Eso es. Del techo.")
    assert control.por_enviar(items) == ""


async def test_un_turno_que_no_llego_a_sonar_se_declara_antes_del_siguiente() -> None:
    """Si el vecino habla antes de que suene la respuesta, LiveKit no informa de esa respuesta.
    El grafo tiene que saber que no se oyó antes de interpretar lo siguiente (si no, un "eso
    es" confirmaría un resumen que el vecino no ha escuchado)."""
    api = _ApiFalsa()
    control = ControlLlamada("llam-1")
    control.nuevo_turno({"turno": 0, "mensajes_bot": ["Hola, ¿en qué finca?"]})
    control.informe()  # el saludo sí sonó
    await control.enviar_turno(api, "Calle Mayor 12")  # su respuesta no llega a sonar
    await control.enviar_turno(api, "sí")
    assert api.llamadas == [
        ("turno", "Calle Mayor 12"),
        ("entrega", (1, True, "")),
        ("turno", "sí"),
    ]


async def test_lo_que_sono_no_se_declara_como_no_reproducido() -> None:
    api = _ApiFalsa()
    control = ControlLlamada("llam-1")
    await control.enviar_turno(api, "Calle Mayor 12")
    assert control.informe() == (1, "respuesta 1")  # LiveKit avisa de que sonó
    await control.enviar_turno(api, "sí")
    assert [x[0] for x in api.llamadas] == ["turno", "turno"]


def _secuencia(*estados: bool):  # type: ignore[no-untyped-def]
    """`hablando()` que devuelve estos estados y luego repite el último."""
    restantes = list(estados)

    def hablando() -> bool:
        return restantes.pop(0) if len(restantes) > 1 else restantes[0]

    return hablando


async def test_si_el_vecino_sigue_hablando_al_cerrar_el_turno_se_espera() -> None:
    """AssemblyAI cerró el turno en una pausa pero el vecino ya ha vuelto a hablar: no se manda
    nada hasta que lleve un rato callado. Las pausas cortas (comas) no cuentan. Si en ese tiempo
    llega su siguiente turno, LiveKit cancela esta espera y el texto va junto con el siguiente
    (`por_enviar`)."""
    control = ControlLlamada("llam-1")
    control.al_cerrar_turno(vecino_hablando=True)
    estados = [True] * 5 + [False] * 2 + [True] * 5 + [False]
    consultas = 0

    def hablando() -> bool:
        nonlocal consultas
        consultas += 1
        return estados[min(consultas, len(estados)) - 1]

    espera = control.esperar_antes_de_enviar("Sí.", hablando, espera_final_s=0.05, paso_s=0.01)
    assert await espera is True
    assert consultas >= len(estados)  # la pausa de dos consultas no acabó la espera
    # El cierre ya se ha consumido: el turno siguiente no espera
    assert await control.esperar_antes_de_enviar("Sí.", hablando) is False


async def test_un_cierre_en_silencio_con_contenido_no_espera_nada() -> None:
    control = ControlLlamada("llam-1")
    control.al_cerrar_turno(vecino_hablando=False)

    def nunca() -> bool:
        raise AssertionError("no debe consultarse")

    assert await control.esperar_antes_de_enviar("Piso 1, puerta B.", nunca) is False


async def test_la_espera_tiene_tope_si_el_ruido_no_para() -> None:
    control = ControlLlamada("llam-1")
    control.al_cerrar_turno(vecino_hablando=True)
    t0 = asyncio.get_running_loop().time()
    espera = control.esperar_antes_de_enviar(
        "Sí.", lambda: True, maximo_s=0.05, espera_final_s=0, paso_s=0.01
    )
    assert await espera is True
    assert asyncio.get_running_loop().time() - t0 < 0.5


async def test_solo_muletillas_espera_a_ver_si_el_vecino_sigue() -> None:
    """ "Ah, vale, perdón." y una pausa: lo normal es que siga ("pues piso uno, puerta A"). Se
    da un margen corto para que vuelva a hablar; si no habla, se manda tal cual."""
    control = ControlLlamada("llam-1")
    hablando = _secuencia(False, False, True, True, False)
    assert await control.esperar_antes_de_enviar(
        "Ah, vale, perdón.", hablando, espera_final_s=0, paso_s=0.001
    )
    t0 = asyncio.get_running_loop().time()
    callado = control.esperar_antes_de_enviar("Mira.", lambda: False, espera_muletilla_s=0.05)
    assert await callado is True
    assert asyncio.get_running_loop().time() - t0 < 0.5


async def test_cuando_el_vecino_calla_se_da_tiempo_a_que_llegue_su_turno() -> None:
    """El VAD da la voz por acabada antes de que AssemblyAI cierre el turno: si se mandara en
    ese instante, el resto llegaría como otro turno."""
    control = ControlLlamada("llam-1")
    control.al_cerrar_turno(vecino_hablando=True)
    t0 = asyncio.get_running_loop().time()
    espera = control.esperar_antes_de_enviar("Sí.", _secuencia(True, False), espera_final_s=0.05)
    assert await espera is True
    assert asyncio.get_running_loop().time() - t0 >= 0.05


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Ah, vale, perdón.", True),
        ("Mira.", True),
        ("Hola, buenas.", True),
        ("Pues...", True),
        ("Sí.", False),  # una respuesta, no una muletilla
        ("Pues sí.", False),
        ("Vale.", False),  # suelto es un "sí"
        ("Bueno.", False),
        ("No.", False),
        ("Piso 1, puerta.", False),
        ("", False),
    ],
)
def test_solo_muletillas(texto: str, esperado: bool) -> None:
    assert solo_muletillas(texto) is esperado


async def test_los_turnos_van_en_orden_aunque_livekit_cancele_el_primero() -> None:
    """LiveKit cancela la generación en curso cuando llega otro turno del vecino. La petición a
    la API no se corta (el grafo ya la está procesando) y la siguiente espera a que acabe."""
    api = _ApiFalsa(espera_s=0.05)
    control = ControlLlamada("llam-1")
    primero = asyncio.ensure_future(control.enviar_turno(api, "Sí."))
    await asyncio.sleep(0.01)
    primero.cancel()
    vista = await control.enviar_turno(api, "Eso es.")
    turnos = [x for x in api.llamadas if x[0] == "turno"]
    assert turnos == [("turno", "Sí."), ("turno", "Eso es.")]
    assert ("entrega", (1, True, "")) in api.llamadas  # la respuesta 1 nunca sonó
    assert vista["turno"] == 2 and control.turno == 2 and control.texto == "respuesta 2"
