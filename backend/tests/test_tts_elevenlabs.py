"""Voz del bot con ElevenLabs: streaming PCM, caché y endpoint de la consola (sin red)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from building_call.telefonia.tts import SintetizadorElevenLabs, crear_tts


def _sintetizador(manejador: Any) -> SintetizadorElevenLabs:
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(manejador))
    return SintetizadorElevenLabs("clave", "voz-1", cliente=cliente)


async def test_pide_pcm_16k_en_espanol_y_entrega_muestras_completas() -> None:
    peticiones: list[httpx.Request] = []

    def manejador(req: httpx.Request) -> httpx.Response:
        peticiones.append(req)
        # 5 bytes: el último es media muestra y no debe salir hasta completarse
        return httpx.Response(200, content=b"\x01\x00\x02\x00\x03")

    tts = _sintetizador(manejador)
    trozos = [t async for t in tts.fragmentos("Hola,   buenas.")]
    assert all(len(t) % 2 == 0 for t in trozos)
    assert b"".join(trozos) == b"\x01\x00\x02\x00"
    req = peticiones[0]
    assert req.url.path == "/v1/text-to-speech/voz-1/stream"
    assert req.url.params["output_format"] == "pcm_16000"
    assert req.headers["xi-api-key"] == "clave"
    assert b'"language_code":"es"' in req.content and b'"text":"Hola, buenas."' in req.content


async def test_frase_repetida_sale_de_la_cache() -> None:
    llamadas = 0

    def manejador(req: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        return httpx.Response(200, content=b"\x00\x01" * 10)

    tts = _sintetizador(manejador)
    assert await tts.sintetizar("Hola") == await tts.sintetizar("Hola")
    assert llamadas == 1


async def test_error_del_proveedor_no_devuelve_audio() -> None:
    tts = _sintetizador(lambda req: httpx.Response(401, json={"detail": "invalid_api_key"}))
    with pytest.raises(RuntimeError, match="401"):
        await tts.sintetizar("Hola")


def test_crear_tts_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    from building_call.settings import settings

    monkeypatch.setattr(settings, "tts", "elevenlabs")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_voz", "v")
    tts = crear_tts(settings)
    assert isinstance(tts, SintetizadorElevenLabs) and tts.disponible()
    monkeypatch.setattr(settings, "elevenlabs_voz", "")
    assert not crear_tts(settings).disponible()


class _VozFalsa:
    nombre = "falsa"
    tasa = 16000

    def __init__(self, falla: bool = False) -> None:
        self.falla = falla

    def disponible(self) -> bool:
        return True

    async def sintetizar(self, texto: str) -> bytes:
        if self.falla:
            raise RuntimeError("caído")
        return b"\x00\x01" * 4


async def _cliente_con_voz(deps: Any, voz: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from conftest import sesion_de_prueba

    from building_call.api import main

    monkeypatch.setattr(main, "crear_tts", lambda settings: voz)
    app = main.crear_app(deps, fabrica_streaming=None)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    c.cookies.set("bc_sesion", sesion_de_prueba(deps.repo))
    return c


async def test_consola_recibe_la_voz_del_servidor(
    deps: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await _cliente_con_voz(deps, _VozFalsa(), monkeypatch) as c:
        r = await c.post("/api/voz-bot", json={"texto": "Hola"})
    assert r.status_code == 200
    assert r.headers["x-tasa"] == "16000"
    assert r.content == b"\x00\x01" * 4


async def test_voz_caida_es_un_502_para_volver_al_navegador(
    deps: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with await _cliente_con_voz(deps, _VozFalsa(falla=True), monkeypatch) as c:
        r = await c.post("/api/voz-bot", json={"texto": "Hola"})
    assert r.status_code == 502


def test_ordinales_se_leen_en_palabras() -> None:
    from building_call.telefonia.tts import texto_para_voz

    assert (
        texto_para_voz("En el 1º las puertas son A o B.") == "En el primero las puertas son A o B."
    )
    assert texto_para_voz("El 3º  B y la 2ª planta") == "El tercero B y la segunda planta"
    assert texto_para_voz("Calle Mayor 12") == "Calle Mayor 12"
