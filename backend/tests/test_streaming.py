"""WebSocket de voz en streaming con una sesión STT falsa, y carrera de modelos."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import sesion_de_prueba
from fastapi.testclient import TestClient

from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias
from building_call.llm.carrera import ModeloCarrera
from building_call.llm.ollama import LLMError
from building_call.stt_streaming import (
    AssemblyAIConfigError,
    EventoTurno,
    SesionStreamingAssemblyAI,
    validar_endpoint_eu,
)


class SesionFalsa:
    """Emite un 'final' cada vez que recibe la orden fin_turno; el texto sale de una cola."""

    textos = ["Calle Mayor 12", "sí"]

    def __init__(self, sample_rate: int, keyterms: list[str] | None = None) -> None:
        self.sample_rate = sample_rate
        self.keyterms = keyterms or []
        self.audio = bytearray()
        self.conexion_ms = 7
        self._cola: asyncio.Queue[EventoTurno | None] = asyncio.Queue()
        self.cerrada = False
        self._i = 0

    async def abrir(self) -> None:
        return None

    async def enviar_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def forzar_fin_turno(self) -> None:
        await self._cola.put(EventoTurno("parcial", "calle ma"))
        await self._cola.put(EventoTurno("final", self.textos[self._i % len(self.textos)]))
        self._i += 1

    async def eventos(self):  # type: ignore[no-untyped-def]
        while True:
            ev = await self._cola.get()
            if ev is None:
                return
            yield ev

    async def cerrar(self) -> None:
        self.cerrada = True
        await self._cola.put(None)


@pytest.fixture
def cliente_ws(deps: Dependencias) -> TestClient:
    c = TestClient(crear_app(deps, fabrica_streaming=SesionFalsa))
    c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
    return c


def test_turno_por_streaming(cliente_ws: TestClient) -> None:
    llamada_id = cliente_ws.post("/api/llamadas", json={}).json()["llamada_id"]
    with cliente_ws.websocket_connect(f"/api/llamadas/{llamada_id}/voz?sample_rate=16000") as ws:
        assert ws.receive_json()["type"] == "listo"
        ws.send_bytes(b"\x00" * 3200)
        ws.send_json({"type": "fin_turno"})
        assert ws.receive_json() == {"type": "parcial", "texto": "calle ma"}
        final = ws.receive_json()
        assert final["type"] == "final" and final["texto"] == "Calle Mayor 12"
        assert "eou_ms" in final
        respuesta = ws.receive_json()
        assert respuesta["type"] == "respuesta"
        assert respuesta["nodo_actual"] == "identificar_finca"
        assert "Calle Mayor 12" in respuesta["mensajes_bot"][0]
        assert "stt_eou_a_respuesta_ms" in respuesta["tiempos"]
        ws.send_json({"type": "pausa"})
        ws.send_json({"type": "fin_turno"})
        ws.receive_json()  # parcial
        ws.receive_json()  # final "sí"
        respuesta = ws.receive_json()
        assert respuesta["datos"]["finca_id"] == "finca-mayor-12"


def test_streaming_sin_configurar_devuelve_error(deps: Dependencias) -> None:
    cliente = TestClient(crear_app(deps, fabrica_streaming=None))
    cliente.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
    llamada_id = cliente.post("/api/llamadas", json={}).json()["llamada_id"]
    with cliente.websocket_connect(f"/api/llamadas/{llamada_id}/voz") as ws:
        assert ws.receive_json()["type"] == "error"
    assert cliente.get("/api/salud").json()["stt_streaming"] is None


def test_solo_endpoints_europeos() -> None:
    assert validar_endpoint_eu("https://api.eu.assemblyai.com/") == "https://api.eu.assemblyai.com"
    with pytest.raises(AssemblyAIConfigError):
        validar_endpoint_eu("https://api.assemblyai.com")
    with pytest.raises(AssemblyAIConfigError):
        SesionStreamingAssemblyAI("clave", host="streaming.assemblyai.com")
    with pytest.raises(AssemblyAIConfigError):
        SesionStreamingAssemblyAI("")


class ModeloLento:
    def __init__(self, nombre: str, retardo: float, resultado: dict[str, Any] | None) -> None:
        self.nombre = nombre
        self.retardo = retardo
        self.resultado = resultado

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:  # noqa: E501
        await asyncio.sleep(self.retardo)
        if self.resultado is None:
            raise LLMError("falló")
        return self.resultado


async def test_carrera_devuelve_el_primero_valido() -> None:
    carrera = ModeloCarrera(
        ModeloLento("lento", 0.2, {"a": 1}),
        ModeloLento("rapido", 0.01, {"a": 2}),
        ModeloLento("roto", 0.0, None),
    )
    assert await carrera.extraer("s", "u", {}) == {"a": 2}
    assert carrera.ganadores["rapido"] == 1


async def test_carrera_falla_si_todos_fallan() -> None:
    carrera = ModeloCarrera(ModeloLento("a", 0.0, None), ModeloLento("b", 0.0, None))
    with pytest.raises(LLMError):
        await carrera.extraer("s", "u", {})


async def test_carrera_espera_al_preferido_si_llega_a_tiempo() -> None:
    preferido = ModeloLento("exacto", 0.05, {"a": "exacto"})
    rapido = ModeloLento("rapido", 0.0, {"a": "rapido"})
    con_margen = ModeloCarrera(preferido, rapido, preferido=0, espera_extra_ms=300)
    assert (await con_margen.extraer("s", "u", {}))["a"] == "exacto"
    sin_margen = ModeloCarrera(preferido, rapido, preferido=0, espera_extra_ms=10)
    assert (await sin_margen.extraer("s", "u", {}))["a"] == "rapido"


async def test_vocabulario_stt_incluye_direcciones_pisos_y_puertas(deps: Dependencias) -> None:
    from building_call.api.main import vocabulario_stt

    terminos = await vocabulario_stt(deps.repo)
    assert "Calle Mayor 12" in terminos and "Avenida de la Alameda 5" in terminos
    assert "puerta A" in terminos and "tercero B" in terminos and "portal 2" in terminos
    assert len(terminos) <= 100


async def test_interpretacion_especulativa_se_reutiliza(deps: Dependencias, llm: Any) -> None:
    import asyncio

    from building_call.graph.nodos import clave_especulacion
    from building_call.llm.interpretacion import Interpretacion
    from building_call.servicios import GestorLlamadas

    gestor = GestorLlamadas(deps)
    estado = await gestor.iniciar()
    for t in ["Calle Mayor 12", "sí"]:
        estado = await gestor.mensaje(estado["llamada_id"], t)
    # El parcial "vivo en el tercero b y me llamo marta" ya se interpretó antes del final
    fut: asyncio.Future[Interpretacion] = asyncio.get_running_loop().create_future()
    fut.set_result(Interpretacion(piso="3", puerta="B", nombre="Marta", origen="llm"))
    deps.especulaciones[estado["llamada_id"]] = {
        clave_especulacion("Vivo en el tercero B y me llamo Marta."): fut
    }
    n = len(llm.llamadas)
    estado = await gestor.mensaje(estado["llamada_id"], "Vivo en el tercero B y me llamo Marta.")
    assert len(llm.llamadas) == n  # no se volvió a llamar al modelo
    assert estado["vivienda_id"] == "finca-mayor-12-u-3B"
    assert estado["nombre_declarado"] == "Marta"
    assert estado["tiempos_turno"]["origen_interpretacion"] == "llm_especulativo"
    assert deps.especulaciones == {}


async def test_especulacion_de_otra_llamada_no_se_usa_ni_se_cancela(
    deps: Dependencias, llm: Any
) -> None:
    """Dos llamadas a la vez: el parcial de una no puede acabar en el expediente de la otra."""
    import asyncio

    from building_call.graph.nodos import clave_especulacion
    from building_call.llm.interpretacion import Interpretacion
    from building_call.servicios import GestorLlamadas

    gestor = GestorLlamadas(deps)
    a = await gestor.iniciar()
    b = await gestor.iniciar()
    for t in ["Calle Mayor 12", "sí"]:
        a = await gestor.mensaje(a["llamada_id"], t)
        b = await gestor.mensaje(b["llamada_id"], t)
    frase = "Vivo en el tercero B y me llamo Marta."
    ajena: asyncio.Future[Interpretacion] = asyncio.get_running_loop().create_future()
    deps.especulaciones[a["llamada_id"]] = {clave_especulacion(frase): ajena}
    b = await gestor.mensaje(b["llamada_id"], frase)
    assert b["tiempos_turno"]["origen_interpretacion"] != "llm_especulativo"
    assert not ajena.cancelled()
    assert a["llamada_id"] in deps.especulaciones


async def test_llamadas_terminadas_o_abandonadas_salen_de_memoria(deps: Dependencias) -> None:
    """El expediente ya está en el repositorio: el estado vivo no debe crecer sin límite."""
    from building_call.servicios import GestorLlamadas, LlamadaNoEncontrada

    ahora = [0.0]
    gestor = GestorLlamadas(deps, reloj=lambda: ahora[0])
    terminada = await gestor.iniciar()
    terminada["finalizada"] = True
    viva = await gestor.iniciar()
    ahora[0] = GestorLlamadas.RETENCION_FINALIZADA_S + 1
    await gestor.iniciar()  # cualquier turno barre lo caducado
    with pytest.raises(LlamadaNoEncontrada):
        gestor.obtener(terminada["llamada_id"])
    assert gestor.obtener(viva["llamada_id"])  # sigue activa: se conserva
    ahora[0] += GestorLlamadas.RETENCION_ABANDONADA_S + 1
    await gestor.iniciar()
    with pytest.raises(LlamadaNoEncontrada):
        gestor.obtener(viva["llamada_id"])


def test_metricas_no_crecen_sin_limite() -> None:
    from building_call.metricas import RegistroMetricas, TiemposTurno

    registro = RegistroMetricas(capacidad=3)
    for i in range(10):
        registro.registrar(
            TiemposTurno(
                llamada_id="l", turno=i, nodo_inicial="a", nodo_final="b", grafo_ms=1, servidor_ms=1
            )
        )
    assert len(registro._indice) == 3
    assert registro.completar_cliente("l", 0, {}) is None
    assert registro.completar_cliente("l", 9, {}) is not None
