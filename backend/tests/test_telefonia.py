"""Telefonía: conversión de audio, puente proveedor-agnóstico, adaptador Twilio y autoconfiguración."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import struct
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from building_call.api.main import crear_app
from building_call.graph.nodos import Dependencias
from building_call.servicios import GestorLlamadas
from building_call.settings import settings as settings_globales
from building_call.stt_streaming import EventoTurno
from building_call.telefonia import audio
from building_call.telefonia.configurar import configurar_numero, motivo_no_listo
from building_call.telefonia.puente import PuenteLlamada
from building_call.telefonia.twilio import (
    RUTA_VOZ,
    firma_valida,
    twiml_conectar_stream,
    url_ws_desde_publica,
)

# ---------------------------------------------------------------- audio


def _tono(tasa: int, ms: int, hz: float = 440.0, amplitud: int = 8000) -> bytes:
    n = int(tasa * ms / 1000)
    return struct.pack(
        f"<{n}h", *(int(amplitud * math.sin(2 * math.pi * hz * i / tasa)) for i in range(n))
    )


def test_entrada_telefono_convierte_ulaw_8k_a_pcm16_16k() -> None:
    ulaw = audio.pcm16_a_ulaw(_tono(8000, 100))
    pcm = audio.EntradaTelefono().convertir(ulaw)
    assert abs(len(pcm) - 100 * 16000 // 1000 * 2) <= 4  # 100 ms a 16 kHz
    assert audio.energia(pcm) > audio.energia(b"\x00" * 3200) == 0


def test_salida_telefono_trocea_en_20ms_y_rellena_el_final() -> None:
    salida = audio.SalidaTelefono(22050)
    trozos = salida.convertir(_tono(22050, 55), final=True)
    assert all(len(t) == 160 for t in trozos)
    assert len(trozos) == 3  # 55 ms → 2 trozos completos + 1 relleno
    assert trozos[-1].endswith(b"\xff")  # silencio μ-law


# ---------------------------------------------------------------- puente


class SttFalso:
    def __init__(self) -> None:
        self.cola: asyncio.Queue[EventoTurno | None] = asyncio.Queue()
        self.audio = bytearray()
        self.cerrado = False

    async def abrir(self) -> None: ...

    async def enviar_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def eventos(self):  # type: ignore[no-untyped-def]
        while (ev := await self.cola.get()) is not None:
            yield ev

    async def cerrar(self) -> None:
        self.cerrado = True
        await self.cola.put(None)


class TtsFalso:
    nombre = "falso"
    tasa = 16000

    def __init__(self) -> None:
        self.textos: list[str] = []

    def disponible(self) -> bool:
        return True

    async def sintetizar(self, texto: str) -> bytes:
        self.textos.append(texto)
        return _tono(16000, 40)  # 2 trozos de 20 ms


class CanalFalso:
    def __init__(self) -> None:
        self.enviados: list[bytes] = []
        self.marcas: list[str] = []
        self.limpiezas = 0
        self.colgado = False

    async def enviar_audio(self, ulaw: bytes) -> None:
        self.enviados.append(ulaw)

    async def marcar(self, nombre: str) -> None:
        self.marcas.append(nombre)

    async def limpiar(self) -> None:
        self.limpiezas += 1

    async def colgar(self) -> None:
        self.colgado = True


@pytest.fixture
def gestor(deps: Dependencias) -> GestorLlamadas:
    return GestorLlamadas(deps)


async def _esperar(cond, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condición no alcanzada")


async def test_puente_saluda_responde_y_cuelga(gestor: GestorLlamadas) -> None:
    stt, tts, canal = SttFalso(), TtsFalso(), CanalFalso()
    puente = PuenteLlamada(
        gestor, lambda _tasa, _vocab: stt, tts, canal, vocabulario=[], telefono="+34600000000"
    )
    llamada_id = await puente.iniciar()
    # Saludo sintetizado y enviado en μ-law con marca del turno 0
    assert len(tts.textos) == 1 and canal.marcas == ["turno-0"]
    assert canal.enviados and all(len(t) == 160 for t in canal.enviados)
    assert (await gestor.obtener(llamada_id))["telefono_llamante"] == "+34600000000"

    # El vecino habla: el audio llega al STT convertido a 16 kHz
    await puente.recibir_ulaw(audio.pcm16_a_ulaw(_tono(8000, 20)))
    assert abs(len(stt.audio) - 640) <= 4  # 20 ms a 16 kHz (ratecv redondea)

    await puente.marca_reproducida("turno-0")
    assert (await gestor.obtener(llamada_id))["entrega_ultima"]["estado"] == "completada"

    # Turno final del STT → grafo → nueva locución
    await stt.cola.put(EventoTurno("final", "Calle Mayor 12"))
    await _esperar(lambda: len(tts.textos) == 2)
    assert canal.marcas[-1] == "turno-1"
    assert (await gestor.obtener(llamada_id))["turno"] == 1
    metricas = gestor.metricas.turnos(llamada_id)
    assert metricas and metricas[-1].stt_cliente == "telefono"

    # Barge-in: parcial de ≥2 palabras mientras suena el bot → clear y entrega interrumpida
    await stt.cola.put(EventoTurno("parcial", "sí claro"))
    await _esperar(lambda: canal.limpiezas == 1)
    assert (await gestor.obtener(llamada_id))["entrega_ultima"]["estado"] == "interrumpida"

    # Conversación hasta el final: al reproducirse la despedida se cuelga
    for texto in ["sí", "3 B", "Gotea el grifo", "sí"]:
        n = len(tts.textos)
        await stt.cola.put(EventoTurno("final", texto))
        await _esperar(lambda n=n: len(tts.textos) > n or puente.finalizada)
    assert puente.finalizada
    await puente.marca_reproducida(canal.marcas[-1])
    assert canal.colgado
    await puente.cerrar()
    assert stt.cerrado


async def test_puente_guarda_las_dos_voces_y_la_mezcla(gestor: GestorLlamadas, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from building_call.audio_store import AlmacenAudio

    repo = gestor.deps.repo
    almacen = AlmacenAudio(tmp_path, "secreto", retencion_dias=14, url_segundos=60)
    stt, tts, canal = SttFalso(), TtsFalso(), CanalFalso()
    puente = PuenteLlamada(
        gestor, lambda _t, _v: stt, tts, canal, vocabulario=[], telefono=None,
        repo=repo, almacen_audio=almacen,
    )  # fmt: skip
    lid = await puente.iniciar()
    await puente.recibir_ulaw(audio.pcm16_a_ulaw(_tono(8000, 200)))
    await puente.cerrar()
    pistas = {p["canal"]: p for p in await repo.listar_pistas(lid)}
    assert sorted(pistas) == ["asistente", "cliente", "mezcla"]
    # La mezcla es lo que se escucha para seguir la llamada: cubre a las dos voces
    assert pistas["mezcla"]["duracion_ms"] >= max(
        pistas["cliente"]["duracion_ms"], pistas["asistente"]["duracion_ms"]
    )


# ---------------------------------------------------------------- twilio


def _firmar(token: str, url: str, params: dict[str, str]) -> str:
    cadena = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), cadena.encode(), hashlib.sha1).digest()
    ).decode()


def test_firma_twilio_y_twiml() -> None:
    params = {"From": "+34600", "CallSid": "CA1"}
    url = "https://bot.example/api/telefonia/twilio/voz"
    assert firma_valida("tok", url, params, _firmar("tok", url, params))
    assert not firma_valida("tok", url, params, _firmar("otro", url, params))
    assert not firma_valida("tok", url, params, None)
    xml = twiml_conectar_stream("wss://bot.example/media", {"telefono": "+34<600"})
    assert '<Stream url="wss://bot.example/media">' in xml and 'value="+34&lt;600"' in xml
    assert (
        url_ws_desde_publica("https://bot.example/")
        == "wss://bot.example/api/telefonia/twilio/media"
    )


@pytest.fixture
async def cliente(deps: Dependencias, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings_globales, "telefonia", "twilio")
    monkeypatch.setattr(settings_globales, "url_publica", "https://bot.example")
    monkeypatch.setattr(settings_globales, "twilio_account_sid", "AC1")
    monkeypatch.setattr(settings_globales, "twilio_auth_token", "tok")
    monkeypatch.setattr(settings_globales, "tts", "ninguno")
    app = crear_app(deps, fabrica_streaming=lambda _t, _v: SttFalso())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as c:
        yield c


async def test_webhook_twilio_rechaza_firma_invalida_y_avisa_si_no_esta_listo(
    cliente: AsyncClient,
) -> None:
    params = {"From": "+34600", "CallSid": "CA1"}
    r = await cliente.post(RUTA_VOZ, data=params, headers={"X-Twilio-Signature": "mal"})
    assert r.status_code == 403
    url = "https://bot.example" + RUTA_VOZ
    r = await cliente.post(
        RUTA_VOZ, data=params, headers={"X-Twilio-Signature": _firmar("tok", url, params)}
    )
    assert r.status_code == 200 and "<Say" in r.text and "<Hangup/>" in r.text  # sin TTS: no listo
    salud = (await cliente.get("/api/telefonia/twilio/salud")).json()
    assert salud["ok"] is False and "TTS" in salud["motivo"]
    assert (await cliente.get("/api/telefonia/estado")).status_code == 401  # protegido


async def test_webhook_twilio_conecta_el_stream_cuando_esta_listo(
    cliente: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("building_call.api.main.motivo_no_listo", lambda *_a, **_k: None)
    params = {"From": "+34600", "CallSid": "CA1"}
    url = "https://bot.example" + RUTA_VOZ
    r = await cliente.post(
        RUTA_VOZ, data=params, headers={"X-Twilio-Signature": _firmar("tok", url, params)}
    )
    assert r.status_code == 200
    assert '<Stream url="wss://bot.example/api/telefonia/twilio/media">' in r.text
    assert '<Parameter name="telefono" value="+34600"/>' in r.text


def test_motivo_no_listo_explica_lo_que_falta() -> None:
    s = SimpleNamespace(
        telefonia="twilio", url_publica="http://x", twilio_account_sid="", twilio_auth_token=""
    )
    assert "https" in str(motivo_no_listo(s, True, True))
    s.url_publica = "https://x"
    assert "TWILIO" in str(motivo_no_listo(s, True, True))
    s.twilio_account_sid = s.twilio_auth_token = "a"
    assert "ASSEMBLYAI" in str(motivo_no_listo(s, True, False))
    assert "TTS" in str(motivo_no_listo(s, False, True))
    assert motivo_no_listo(s, True, True) is None


async def test_configurar_numero_fija_los_webhooks() -> None:
    peticiones: list[tuple[str, str, dict[str, Any]]] = []

    def responder(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200, json={"incoming_phone_numbers": [{"sid": "PN1", "phone_number": "+34911"}]}
            )
        datos = dict(p.split("=", 1) for p in req.content.decode().split("&"))
        peticiones.append((req.method, str(req.url), datos))
        return httpx.Response(
            200,
            json={
                "phone_number": "+34911",
                "voice_url": datos["VoiceUrl"].replace("%3A", ":").replace("%2F", "/"),
                "status_callback": "x",
            },
        )

    s = SimpleNamespace(
        twilio_account_sid="AC1",
        twilio_auth_token="tok",
        twilio_numero="+34911",
        url_publica="https://bot.example/",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as c:
        resumen = await configurar_numero(s, c)
    assert resumen["numero"] == "+34911"
    assert resumen["voice_url"] == "https://bot.example/api/telefonia/twilio/voz"
    assert peticiones[0][1].endswith("/IncomingPhoneNumbers/PN1.json")
    assert peticiones[0][2]["VoiceMethod"] == "POST"


def test_evento_json_de_twilio_se_decodifica_como_esperamos() -> None:
    # Formato de referencia de Media Streams; si Twilio lo cambia, este test lo detecta antes que una llamada real
    m = json.loads(
        '{"event":"media","streamSid":"MZ1","media":{"track":"inbound","payload":"//8="}}'
    )
    assert base64.b64decode(m["media"]["payload"]) == b"\xff\xff"


class GestorQueFalla(GestorLlamadas):
    """Gestor real cuyo turno revienta (LLM caído, timeout...) las veces indicadas."""

    def __init__(self, deps: Dependencias, fallos: int) -> None:
        super().__init__(deps)
        self.fallos = fallos

    async def mensaje(self, llamada_id: str, texto: str, stt_ms: int | None = None):  # type: ignore[no-untyped-def]
        if self.fallos:
            self.fallos -= 1
            raise RuntimeError("LLM caído")
        return await super().mensaje(llamada_id, texto, stt_ms)


async def test_puente_pide_repetir_si_un_turno_falla(deps: Dependencias) -> None:
    gestor = GestorQueFalla(deps, fallos=1)
    stt, tts, canal = SttFalso(), TtsFalso(), CanalFalso()
    puente = PuenteLlamada(gestor, lambda _t, _v: stt, tts, canal, vocabulario=[], telefono=None)
    llamada_id = await puente.iniciar()
    await stt.cola.put(EventoTurno("final", "Calle Mayor 12"))
    await _esperar(lambda: len(tts.textos) == 2)
    assert "repet" in tts.textos[-1].lower()
    assert not puente.finalizada and not canal.colgado
    # El siguiente turno funciona con normalidad
    await stt.cola.put(EventoTurno("final", "Calle Mayor 12"))
    await _esperar(lambda: gestor.obtener_local(llamada_id)["turno"] == 1)
    await puente.cerrar()


async def test_puente_cierra_con_disculpa_y_deja_la_llamada_para_revisar(
    deps: Dependencias,
) -> None:
    gestor = GestorQueFalla(deps, fallos=2)
    stt, tts, canal = SttFalso(), TtsFalso(), CanalFalso()
    puente = PuenteLlamada(gestor, lambda _t, _v: stt, tts, canal, vocabulario=[], telefono=None)
    llamada_id = await puente.iniciar()
    for _ in range(2):
        n = len(tts.textos)
        await stt.cola.put(EventoTurno("final", "Calle Mayor 12"))
        await _esperar(lambda n=n: len(tts.textos) > n)
    assert puente.finalizada
    await puente.marca_reproducida(canal.marcas[-1])
    assert canal.colgado
    guardada = await deps.repo.obtener_llamada(llamada_id)  # type: ignore[attr-defined]
    assert guardada.estado == "finalizada" and guardada.requiere_revision
    assert guardada.motivo_revision
    await puente.cerrar()


async def test_puente_se_disculpa_si_el_stt_se_corta(gestor: GestorLlamadas) -> None:
    stt, tts, canal = SttFalso(), TtsFalso(), CanalFalso()
    puente = PuenteLlamada(gestor, lambda _t, _v: stt, tts, canal, vocabulario=[], telefono=None)
    llamada_id = await puente.iniciar()
    await stt.cola.put(None)  # AssemblyAI cierra el stream sin que nadie haya colgado
    await _esperar(lambda: puente.finalizada)
    assert len(tts.textos) == 2
    assert (await gestor.obtener(llamada_id))["finalizada"]
    await puente.cerrar()


def test_el_stream_de_twilio_exige_el_token_de_su_llamada(
    deps: Dependencias, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión de seguridad 2026-10-22: cualquiera podía abrir el WebSocket de medios y hablar
    con el bot (gastando STT, LLM y TTS). Ahora el stream lleva un token HMAC de su CallSid que
    solo pone nuestro webhook firmado."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from building_call.telefonia.twilio import RUTA_MEDIA, token_stream

    monkeypatch.setattr(settings_globales, "twilio_auth_token", "tok")
    monkeypatch.setattr("building_call.api.main.motivo_no_listo", lambda *_a, **_k: None)
    abiertos: list[str] = []
    app = crear_app(deps, fabrica_streaming=None)
    with TestClient(app) as c:
        for parametros in ({}, {"call_sid": "CA1", "token": "falso"}):
            with c.websocket_connect(RUTA_MEDIA) as ws:
                ws.send_json(
                    {
                        "event": "start",
                        "start": {"streamSid": "MZ1", "customParameters": parametros},
                    }
                )
                with pytest.raises(WebSocketDisconnect) as cierre:
                    ws.receive_json()
                assert cierre.value.code == 1008
                abiertos.append("cerrado")
    assert abiertos == ["cerrado", "cerrado"]
    assert token_stream("tok", "CA1") != token_stream("tok", "CA2")
    assert token_stream("tok", "CA1") != token_stream("otro", "CA1")


async def test_el_webhook_firma_el_stream_con_el_callsid(
    cliente: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from building_call.telefonia.twilio import token_stream

    monkeypatch.setattr("building_call.api.main.motivo_no_listo", lambda *_a, **_k: None)
    params = {"From": "+34600", "CallSid": "CA1"}
    url = "https://bot.example" + RUTA_VOZ
    r = await cliente.post(
        RUTA_VOZ, data=params, headers={"X-Twilio-Signature": _firmar("tok", url, params)}
    )
    assert f'<Parameter name="token" value="{token_stream("tok", "CA1")}"/>' in r.text
