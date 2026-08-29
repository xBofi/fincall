"""Grabación alineada de la llamada: línea de tiempo común, mezcla y voz del bot en la consola web."""

from __future__ import annotations

import struct
from typing import Any

import pytest
from conftest import sesion_de_prueba
from fastapi.testclient import TestClient
from test_streaming import SesionFalsa

from building_call.api.main import crear_app
from building_call.grabacion import GrabadorConversacion
from building_call.graph.nodos import Dependencias
from building_call.settings import settings


class Reloj:
    """Reloj manual: la colocación en el tiempo se prueba sin dormir."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def avanzar(self, segundos: float) -> None:
        self.t += segundos


def pcm(ms: int, tasa: int = 16000, valor: int = 10000) -> bytes:
    return struct.pack("<h", valor) * int(tasa * ms / 1000)


def muestras(datos: bytes) -> tuple[int, ...]:
    return struct.unpack(f"<{len(datos) // 2}h", datos)


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def grabador(reloj: Reloj) -> GrabadorConversacion:
    return GrabadorConversacion(reloj=reloj)


# ---------------------------------------------------------------- línea de tiempo


def test_el_jitter_no_estira_la_pista(grabador: GrabadorConversacion, reloj: Reloj) -> None:
    """Los trozos llegan con retraso variable, pero el audio es continuo: se escribe seguido."""
    reloj.avanzar(0.15)
    grabador.escribir_cliente(pcm(100))
    reloj.avanzar(0.20)
    grabador.escribir_cliente(pcm(100))
    assert grabador.duracion_ms() == 200


def test_un_hueco_real_se_rellena_con_silencio(
    grabador: GrabadorConversacion, reloj: Reloj
) -> None:
    """Dos segundos sin audio (el vecino calla mientras habla el bot) siguen ocupando su sitio."""
    grabador.escribir_cliente(pcm(100))
    reloj.avanzar(2.0)
    grabador.escribir_cliente(pcm(100))
    assert grabador.duracion_ms() == 2100
    pista = grabador.pistas()["cliente"]
    assert set(muestras(pista[3200:32000])) == {0}


def test_el_asistente_se_ancla_a_la_marca(grabador: GrabadorConversacion, reloj: Reloj) -> None:
    """El bot se sintetiza al cerrar, pero se coloca donde empezó a sonar."""
    reloj.avanzar(1.0)
    marca = grabador.marcar()
    assert marca == 32000  # 1 s a 16 kHz PCM16
    grabador.escribir_asistente(pcm(200), desde=marca)
    pista = grabador.pistas()["asistente"]
    assert set(muestras(pista[:32000])) == {0}
    assert len(pista) == 32000 + 6400


def test_un_turno_del_bot_no_pisa_al_anterior(grabador: GrabadorConversacion) -> None:
    """Si una marca cae dentro de un turno ya escrito, el nuevo va detrás en lugar de encima."""
    grabador.escribir_asistente(pcm(200), desde=0)
    grabador.escribir_asistente(pcm(100), desde=3200)
    pista = grabador.pistas()["asistente"]
    assert len(pista) == 6400 + 3200
    assert set(muestras(pista)) == {10000}  # nada se sobrescribió con silencio


# ---------------------------------------------------------------- mezcla


def test_la_mezcla_suma_las_dos_voces_y_satura(grabador: GrabadorConversacion) -> None:
    """Si coinciden dos voces fuertes, recorta en el límite de int16; nunca da la vuelta."""
    grabador.escribir_cliente(pcm(100, valor=20000))
    grabador.escribir_asistente(pcm(100, valor=20000), desde=0)
    assert set(muestras(grabador.mezcla())) == {32767}


def test_la_mezcla_dura_lo_que_la_pista_mas_larga(grabador: GrabadorConversacion) -> None:
    grabador.escribir_cliente(pcm(200))
    grabador.escribir_asistente(pcm(50), desde=0)
    assert len(grabador.mezcla()) == 6400


def test_pistas_pone_la_mezcla_primero_y_omite_las_vacias(
    grabador: GrabadorConversacion,
) -> None:
    assert grabador.pistas() == {}
    grabador.escribir_cliente(pcm(100))
    assert list(grabador.pistas()) == ["mezcla", "cliente"]  # sin `asistente`, que está vacía


def test_todo_se_guarda_a_16k_venga_de_donde_venga(grabador: GrabadorConversacion) -> None:
    """El teléfono entrega 8 kHz y Piper 22,05 kHz; la pista siempre sale a 16 kHz."""
    grabador.escribir_cliente(pcm(100, tasa=8000), 8000)
    grabador.escribir_asistente(pcm(100, tasa=22050), 22050, desde=0)
    assert abs(grabador.duracion_ms() - 100) <= 5  # ratecv redondea


# ---------------------------------------------------------------- consola web


class TtsDeServidor:
    """TTS de servidor falso: devuelve 100 ms de tono por frase y apunta lo que le piden."""

    nombre = "falso/servidor"
    tasa = 16000

    def __init__(self) -> None:
        self.frases: list[str] = []

    def disponible(self) -> bool:
        return True

    async def sintetizar(self, texto: str) -> bytes:
        self.frases.append(texto)
        return pcm(100)


class TtsAusente(TtsDeServidor):
    def disponible(self) -> bool:
        return False


def _cliente(deps: Dependencias, tts: Any, tmp_path: Any, monkeypatch: Any) -> TestClient:
    monkeypatch.setattr(settings, "audio_dir", str(tmp_path))
    monkeypatch.setattr("building_call.api.main.crear_tts", lambda _s: tts)
    c = TestClient(crear_app(deps, fabrica_streaming=SesionFalsa))
    c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))  # type: ignore[arg-type]
    return c


def _conversacion(cliente: TestClient) -> str:
    """Un turno completo como lo haría el navegador: audio, respuesta, pausa y ledger."""
    llamada_id = cliente.post("/api/llamadas", json={}).json()["llamada_id"]
    with cliente.websocket_connect(f"/api/llamadas/{llamada_id}/voz?sample_rate=16000") as ws:
        assert ws.receive_json()["type"] == "listo"
        ws.send_bytes(pcm(200))
        ws.send_json({"type": "fin_turno"})
        ws.receive_json()  # parcial
        ws.receive_json()  # final
        respuesta = ws.receive_json()
        assert respuesta["type"] == "respuesta"
        # El navegador silencia el micro, habla, y luego informa de lo que llegó a sonar
        ws.send_json({"type": "pausa"})
        ws.send_bytes(pcm(200))  # el vecino sigue sonando mientras habla el bot
        ws.send_json(
            {
                "type": "entrega",
                "turno": respuesta["turno"],
                "estado": "completada",
                "ratio": 1.0,
                "texto_entregado": "Gracias, tomo nota.",
            }
        )
        ws.send_json({"type": "reanudar"})
    return str(llamada_id)


async def test_la_consola_web_graba_tambien_al_asistente(
    deps: Dependencias, tmp_path: Any, monkeypatch: Any
) -> None:
    """El bot habla por el navegador: su pista se reconstruye con el TTS de servidor.

    Antes de 2026-08-29 el expediente solo guardaba `cliente` y sonaba como un monólogo.
    """
    tts = TtsDeServidor()
    llamada_id = _conversacion(_cliente(deps, tts, tmp_path, monkeypatch))

    pistas = await deps.repo.listar_pistas(llamada_id)  # type: ignore[attr-defined]
    assert sorted(p["canal"] for p in pistas) == ["asistente", "cliente", "mezcla"]
    # Se sintetizó el saludo del turno 0 y el texto que el navegador dijo haber entregado
    assert "Gracias, tomo nota." in tts.frases
    assert len(tts.frases) == 2

    por_canal = {p["canal"]: p for p in pistas}
    assert por_canal["mezcla"]["duracion_ms"] >= por_canal["cliente"]["duracion_ms"]


async def test_el_vecino_se_graba_tambien_mientras_habla_el_bot(
    deps: Dependencias, tmp_path: Any, monkeypatch: Any
) -> None:
    """Los 200 ms enviados en pausa cuentan: si se descartan, la pista pierde el barge-in y los
    tiempos dejan de cuadrar con la mezcla."""
    llamada_id = _conversacion(_cliente(deps, TtsDeServidor(), tmp_path, monkeypatch))
    pistas = {p["canal"]: p for p in await deps.repo.listar_pistas(llamada_id)}  # type: ignore[attr-defined]
    assert pistas["cliente"]["duracion_ms"] >= 400


async def test_sin_tts_de_servidor_se_guarda_igual_la_voz_del_vecino(
    deps: Dependencias, tmp_path: Any, monkeypatch: Any
) -> None:
    """Degradación: sin TTS no hay pista del asistente, pero no se pierde la llamada."""
    llamada_id = _conversacion(_cliente(deps, TtsAusente(), tmp_path, monkeypatch))
    pistas = await deps.repo.listar_pistas(llamada_id)  # type: ignore[attr-defined]
    assert sorted(p["canal"] for p in pistas) == ["cliente", "mezcla"]
