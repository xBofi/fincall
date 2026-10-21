"""El estado vivo de la llamada sobrevive a un reinicio de la API (y admite varias instancias).

El reinicio se simula con dos `GestorLlamadas` que comparten el mismo almacén: el primero empieza
la llamada y el segundo, nuevo y sin nada en memoria, la continúa.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import pytest
from conftest import LLMFalso
from sqlalchemy.dialects import postgresql

from building_call.graph.nodos import Dependencias
from building_call.repos.estado_llamada import (
    AlmacenEstadoMemoria,
    AlmacenEstadoPostgres,
    sentencia_guardar,
    sentencia_purgar,
)
from building_call.repos.memoria import RepositorioMemoria
from building_call.repos.modelos_sql import EstadoLlamadaSQL
from building_call.servicios import GestorLlamadas, LlamadaNoEncontrada

TURNOS = ["Vivo en la calle Mayor número 12", "sí", "tercero B", "Me llamo Ana"]


def _repo() -> RepositorioMemoria:
    r = RepositorioMemoria()
    r.config_administrador["adm-demo"] = r.config_administrador["adm-demo"].model_copy(
        update={"pedir_detalle": False}
    )
    return r


async def _seguir(gestor: GestorLlamadas, llamada_id: str, turnos: list[str]) -> dict[str, Any]:
    estado: dict[str, Any] = {}
    for texto in turnos:
        estado = await gestor.mensaje(llamada_id, texto)
    return estado


@pytest.mark.parametrize("serializar", [False, True], ids=["referencias", "json"])
async def test_la_llamada_continua_en_un_gestor_nuevo(serializar: bool) -> None:
    """Con `serializar=True` el estado hace el mismo viaje que en PostgreSQL (JSONB)."""
    almacen = AlmacenEstadoMemoria(serializar=serializar)
    repo_a, repo_b = _repo(), _repo()
    antes = GestorLlamadas(Dependencias(repo=repo_a, llm=LLMFalso()), almacen=almacen)
    inicio = await antes.iniciar()
    llamada_id = inicio["llamada_id"]
    await _seguir(antes, llamada_id, TURNOS[:2])

    despues = GestorLlamadas(Dependencias(repo=repo_b, llm=LLMFalso()), almacen=almacen)
    estado = await _seguir(despues, llamada_id, [*TURNOS[2:], "Gotea el grifo de la cocina", "sí"])

    # La misma llamada sin pasos repetidos: los datos del primer proceso siguen ahí
    assert estado["finca_id"] == "finca-mayor-12"
    assert estado["vivienda_etiqueta"]
    assert estado["turno"] == 6
    assert estado["resultado"] == "incidencia_creada"
    textos = [h["texto"] for h in estado["historial"] if h["rol"] == "vecino"]
    assert textos[:2] == TURNOS[:2]
    # El gestor nuevo también sabe de ella sin haberla empezado
    assert (await despues.obtener(llamada_id))["finalizada"] is True


async def test_entrega_registrada_antes_del_reinicio_se_usa_despues() -> None:
    almacen = AlmacenEstadoMemoria(serializar=True)
    antes = GestorLlamadas(Dependencias(repo=_repo(), llm=LLMFalso()), almacen=almacen)
    estado = await antes.iniciar()
    texto = estado["ultimo_mensaje_bot"]
    await antes.registrar_entrega(
        estado["llamada_id"], estado["turno"], "interrumpida", 0.1, texto[:10]
    )
    despues = GestorLlamadas(Dependencias(repo=_repo(), llm=LLMFalso()), almacen=almacen)
    recuperado = await despues.obtener(estado["llamada_id"])
    assert recuperado["entrega_ultima"]["estado"] == "interrumpida"
    assert recuperado["entrega_ultima"]["texto_entregado"] == texto[:10]


async def test_el_gestor_usa_el_almacen_del_repositorio() -> None:
    repo = _repo()
    antes = GestorLlamadas(Dependencias(repo=repo, llm=LLMFalso()))
    estado = await antes.iniciar()
    despues = GestorLlamadas(Dependencias(repo=repo, llm=LLMFalso()))
    assert (await despues.obtener(estado["llamada_id"]))["turno"] == 0
    assert isinstance(repo.estado_llamadas, AlmacenEstadoMemoria)


async def test_llamada_desconocida_no_se_encuentra() -> None:
    gestor = GestorLlamadas(Dependencias(repo=_repo(), llm=LLMFalso()))
    with pytest.raises(LlamadaNoEncontrada):
        await gestor.obtener("llam-inexistente")
    with pytest.raises(LlamadaNoEncontrada):
        gestor.obtener_local("llam-inexistente")


class AlmacenContado(AlmacenEstadoMemoria):
    def __init__(self) -> None:
        super().__init__()
        self.lecturas = 0
        self.escrituras = 0

    async def obtener(self, llamada_id: str) -> Any:
        self.lecturas += 1
        return await super().obtener(llamada_id)

    async def guardar(self, estado: Any, ahora: float) -> None:
        self.escrituras += 1
        await super().guardar(estado, ahora)


async def test_un_turno_hace_como_mucho_una_lectura_y_una_escritura() -> None:
    almacen = AlmacenContado()
    gestor = GestorLlamadas(Dependencias(repo=_repo(), llm=LLMFalso()), almacen=almacen)
    estado = await gestor.iniciar()
    assert (almacen.lecturas, almacen.escrituras) == (0, 1)
    await gestor.mensaje(estado["llamada_id"], TURNOS[0])
    assert (almacen.lecturas, almacen.escrituras) == (1, 2)


async def test_descartar_quita_el_estado() -> None:
    almacen = AlmacenEstadoMemoria()
    await almacen.guardar({"llamada_id": "llam-1", "turno": 0}, 0.0)  # type: ignore[typeddict-item]
    await almacen.descartar("llam-1")
    assert await almacen.obtener("llam-1") is None


async def test_purga_de_otro_proceso_respeta_la_retencion() -> None:
    almacen = AlmacenEstadoMemoria()
    await almacen.guardar({"llamada_id": "fin", "finalizada": True}, 0.0)  # type: ignore[typeddict-item]
    await almacen.guardar({"llamada_id": "viva", "finalizada": False}, 0.0)  # type: ignore[typeddict-item]
    rf, ra = GestorLlamadas.RETENCION_FINALIZADA_S, GestorLlamadas.RETENCION_ABANDONADA_S
    assert await almacen.purgar(rf + 1, rf, ra) == ["fin"]
    assert await almacen.obtener("viva") is not None
    assert await almacen.purgar(ra + 1, rf, ra) == ["viva"]


async def test_el_almacen_en_memoria_cuesta_microsegundos() -> None:
    """Presupuesto de latencia: guardar + leer el estado de una llamada larga, muy por debajo del
    milisegundo en memoria (el turno con LLM ronda 1 s; esto no debe notarse)."""
    gestor = GestorLlamadas(Dependencias(repo=_repo(), llm=LLMFalso()))
    estado = await _seguir(gestor, (await gestor.iniciar())["llamada_id"], TURNOS)
    almacen = AlmacenEstadoMemoria()
    n = 2000
    t0 = perf_counter()
    for i in range(n):
        await almacen.guardar(estado, float(i))
        await almacen.obtener(estado["llamada_id"])
    media_ms = (perf_counter() - t0) * 1000 / n
    assert media_ms < 0.5, f"{media_ms:.3f} ms por turno"


@pytest.mark.parametrize("serializar", [False, True], ids=["referencias", "json"])
async def test_un_estado_de_un_turno_anterior_no_pisa_al_nuevo(serializar: bool) -> None:
    """Gana la escritura del turno más reciente, no la última en llegar (y una llamada terminada
    no vuelve a estar en curso). Así se comporta también el upsert de PostgreSQL."""
    almacen = AlmacenEstadoMemoria(serializar=serializar)
    nuevo: Any = {"llamada_id": "llam-1", "turno": 2, "finalizada": False}
    assert await almacen.guardar(nuevo, 1.0) is True
    assert await almacen.guardar({**nuevo, "turno": 1, "viejo": True}, 2.0) is False
    assert await almacen.guardar({**nuevo, "finalizada": True}, 3.0) is True
    assert await almacen.guardar({**nuevo, "entrega_ultima": {}}, 4.0) is False
    guardado = await almacen.obtener("llam-1")
    assert guardado is not None
    assert (guardado["turno"], guardado["finalizada"]) == (2, True)


class AlmacenLento(AlmacenEstadoMemoria):
    """Como PostgreSQL: leer cede el control y otro turno puede escribir mientras tanto."""

    def __init__(self) -> None:
        super().__init__(serializar=True)
        self.leido = asyncio.Event()
        self.seguir = asyncio.Event()
        self.pausar = False

    async def obtener(self, llamada_id: str) -> Any:
        estado = await super().obtener(llamada_id)
        if self.pausar:
            self.pausar = False
            self.leido.set()
            await self.seguir.wait()
        return estado


async def test_una_entrega_tardia_no_deshace_el_turno_siguiente() -> None:
    """La entrega del turno N lee el estado, y antes de escribirlo el turno N+1 ya se guardó:
    la entrega no puede devolver la llamada al turno N."""
    almacen = AlmacenLento()
    gestor = GestorLlamadas(Dependencias(repo=_repo(), llm=LLMFalso()), almacen=almacen)
    estado = await gestor.iniciar()
    llamada_id = estado["llamada_id"]
    almacen.pausar = True
    entrega = asyncio.create_task(
        gestor.registrar_entrega(llamada_id, 0, "interrumpida", 0.5, "Hola")
    )
    await almacen.leido.wait()
    siguiente = await gestor.mensaje(llamada_id, TURNOS[0])
    almacen.seguir.set()
    devuelto = await entrega
    assert devuelto["turno"] == siguiente["turno"] == 1
    assert (await gestor.obtener(llamada_id))["turno"] == 1
    assert gestor.obtener_local(llamada_id)["turno"] == 1


# ------------------------------------------------------------------------------- PostgreSQL


def test_modelo_sql_del_estado() -> None:
    tabla = EstadoLlamadaSQL.__table__
    assert EstadoLlamadaSQL.__tablename__ == "estados_llamada"
    assert {c.name for c in tabla.columns} == {
        "llamada_id",
        "estado",
        "turno",
        "version",
        "finalizada",
        "actualizada_en",
    }
    assert isinstance(tabla.c.estado.type.dialect_impl(postgresql.dialect()), postgresql.JSONB)


def _sql(sentencia: Any) -> str:
    return str(sentencia.compile(dialect=postgresql.dialect()))


def test_guardar_es_un_upsert_que_sube_la_version() -> None:
    sql = _sql(sentencia_guardar({"llamada_id": "llam-1", "turno": 3}, 10.0))  # type: ignore[typeddict-item]
    assert "INSERT INTO estados_llamada" in sql
    assert "ON CONFLICT (llamada_id) DO UPDATE" in sql
    assert "version = (estados_llamada.version + " in sql
    # Un estado de un turno anterior (o que reabre una llamada terminada) no pisa al guardado
    assert "WHERE excluded.turno >= estados_llamada.turno" in sql
    assert "RETURNING estados_llamada.llamada_id" in sql


def test_purgar_borra_con_la_misma_politica_y_devuelve_los_ids() -> None:
    sql = _sql(sentencia_purgar(10_000.0, 900, 7200))
    assert sql.startswith("DELETE FROM estados_llamada")
    assert "estados_llamada.finalizada" in sql
    assert "RETURNING estados_llamada.llamada_id" in sql


def test_guardar_pone_la_hora_en_utc() -> None:
    sentencia = sentencia_guardar({"llamada_id": "llam-1", "turno": 0}, 0.0)  # type: ignore[typeddict-item]
    valores = sentencia.compile(dialect=postgresql.dialect()).params
    assert valores["actualizada_en"] == datetime.fromtimestamp(0.0, UTC)
    assert valores["version"] == 1


@pytest.mark.skipif(
    not os.environ.get("BC_TEST_DATABASE_URL"),
    reason="integración: necesita BC_TEST_DATABASE_URL apuntando a una base de pruebas",
)
async def test_integracion_postgres_guardar_leer_y_purgar() -> None:  # pragma: no cover
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(os.environ["BC_TEST_DATABASE_URL"])
    async with engine.begin() as con:
        await con.run_sync(EstadoLlamadaSQL.__table__.create, checkfirst=True)
    almacen = AlmacenEstadoPostgres(async_sessionmaker(engine, expire_on_commit=False))
    estado: Any = {"llamada_id": "llam-test-integracion", "turno": 2, "finalizada": True}
    try:
        await almacen.guardar(estado, 0.0)
        assert await almacen.guardar({**estado, "turno": 3}, 1.0) is True
        assert await almacen.guardar({**estado, "turno": 1}, 2.0) is False
        assert (await almacen.obtener("llam-test-integracion"))["turno"] == 3  # type: ignore[index]
        assert "llam-test-integracion" in await almacen.purgar(10_000.0, 900, 7200)
        assert await almacen.obtener("llam-test-integracion") is None
    finally:
        await almacen.descartar("llam-test-integracion")
        await engine.dispose()
