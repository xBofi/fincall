from __future__ import annotations

from typing import Any

from building_call.graph.nodos import Dependencias
from building_call.llm.redaccion import conserva_hechos, hechos_obligatorios, redactar
from building_call.servicios import GestorLlamadas


def test_hechos_obligatorios_extrae_numeros_referencias_y_nombres() -> None:
    plantilla = (
        "He identificado Avenida de la Alameda 5. ¿Es correcto? Perfecto, 3º B. "
        "He registrado su incidencia con referencia inc-abc123. Se avisará a Fontanería Pérez."
    )
    hechos = hechos_obligatorios(plantilla)
    assert "5" in hechos and "3º" in hechos and "inc-abc123" in hechos
    assert "Avenida" in " ".join(hechos) and "Fontanería Pérez" in hechos


def test_conserva_hechos_detecta_omisiones() -> None:
    plantilla = "He identificado Calle Mayor 12. ¿Es correcto?"
    assert conserva_hechos(plantilla, "Entendido, ¿vive usted en Calle Mayor 12?")
    assert not conserva_hechos(plantilla, "Entendido, ¿vive usted en Calle Mayor?")
    assert not conserva_hechos(plantilla, "Vive en Calle Mayor 12.")  # perdió la pregunta


class RedactorFalso:
    nombre = "redactor"

    def __init__(self, respuesta: str | None, tarda: float = 0) -> None:
        self.respuesta = respuesta
        self.tarda = tarda

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:  # noqa: E501
        import asyncio

        await asyncio.sleep(self.tarda)
        if self.respuesta is None:
            raise RuntimeError("caído")
        return {"respuesta": self.respuesta}


async def test_redactar_usa_plantilla_si_pierde_hechos_o_falla() -> None:
    plantilla = "He identificado Calle Mayor 12. ¿Es correcto?"
    texto, m = await redactar(RedactorFalso("¿Vive en Calle Mayor?"), plantilla, "en mayor", {})
    assert texto == plantilla and m["redaccion"].startswith("plantilla")
    texto, m = await redactar(RedactorFalso(None), plantilla, "en mayor", {})
    assert texto == plantilla and m["redaccion"].startswith("fallo")
    texto, m = await redactar(
        RedactorFalso("x", tarda=0.3), plantilla, "en mayor", {}, timeout_s=0.05
    )
    assert texto == plantilla
    texto, m = await redactar(
        RedactorFalso("Entendido. ¿Vive usted en Calle Mayor 12?"), plantilla, "en mayor", {}
    )
    assert texto == "Entendido. ¿Vive usted en Calle Mayor 12?" and m["redaccion"] == "llm"


async def test_gestor_redacta_solo_turnos_interpretados_por_llm(deps: Dependencias) -> None:
    gestor = GestorLlamadas(deps, redactor=deps.llm)  # el LLM falso antepone "Vale. "
    estado = await gestor.iniciar()
    assert estado["mensajes_bot"][0].startswith("Hola")  # saludo: plantilla tal cual
    estado = await gestor.mensaje(estado["llamada_id"], "Calle Mayor 12")
    assert estado["mensajes_bot"] == [
        "He identificado Calle Mayor 12. ¿Es correcto?"
    ]  # determinista
    estado = await gestor.mensaje(estado["llamada_id"], "sí")
    estado = await gestor.mensaje(
        estado["llamada_id"], "Pues mire, vivo en el tercero B y me llamo Marta, y hay una fuga"
    )
    # Turno interpretado por el LLM: se redacta y el historial guarda lo dicho
    assert estado["mensajes_bot"][0].startswith("Vale. ")
    assert estado["historial"][-1]["texto"] == estado["mensajes_bot"][0]
    assert estado["historial"][-2]["rol"] == "vecino"
    assert estado["tiempos_turno"]["redaccion"] == "llm"
