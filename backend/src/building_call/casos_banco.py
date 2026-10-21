"""Casos del banco de pruebas marcados por calidad desde el expediente de una llamada.

Convertir cada fallo real en un caso de `evals/gold_interpretacion.jsonl` o
`evals/gold_clasificacion.jsonl` es lo que más ha mejorado al asistente. Aquí se valida lo que
el revisor dice que el bot debería haber entendido y se construye el caso a partir del turno
guardado, sin datos personales: nunca el teléfono y el nombre del vecino sustituido por uno
ficticio. `evals/exportar_casos.py` los pasa al formato del banco.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from building_call.domain.configuracion import URGENCIAS
from building_call.domain.entidades import ACTUACIONES
from building_call.llm.interpretacion import INTENCIONES

Banco = Literal["interpretacion", "clasificacion"]

# Las claves de `Interpretacion` que el banco puede esperar (las de `comparar_modelos.py`)
CLAVES_ESPERADO: tuple[str, ...] = (
    "intencion", "confirma", "direccion", "portal", "piso", "puerta", "nombre", "problema",
    "pregunta", "categoria", "urgencia", "resumen", "actuacion", "equipo", "objecion",
)  # fmt: skip

_ENUMS: dict[str, tuple[str, ...]] = {
    "intencion": INTENCIONES,
    "urgencia": URGENCIAS,
    "actuacion": ACTUACIONES,
}

# Nodos donde aún no se conoce la finca: el turno real no llevaba la lista de equipos
_NODOS_SIN_FINCA = {"inicio", "identificar_finca"}

NOMBRE_FICTICIO = ("Carmen", "Ruiz", "Gómez")
TELEFONO_FICTICIO = "600 000 000"
_TELEFONO = re.compile(r"(?<![\d+])(?:\+\s?34[\s.-]?)?\d(?:[\s.-]?\d){8}(?!\d)")
_PARTICULAS = {"de", "del", "la", "las", "los", "y", "i"}


class CasoBancoEntrada(BaseModel):
    """Lo que manda el revisor al marcar un turno del vecino."""

    turno: int
    esperado: dict[str, Any]
    nota: str | None = Field(default=None, max_length=2000)
    banco: Banco = "interpretacion"
    # Si el vecino cortó al bot, lo que llegó a oír (ledger de entrega)
    oido: str | None = Field(default=None, max_length=2000)
    # Por si la pregunta deducida del turno anterior no es la que contestaba
    pregunta: str | None = Field(default=None, max_length=2000)


class CasoInvalido(ValueError):
    pass


def _valor_valido(clave: str, valor: Any) -> bool:
    if valor is None or valor == "*":
        return clave != "confirma" or valor is None
    if clave == "confirma":
        return isinstance(valor, bool)
    if isinstance(valor, bool) or not isinstance(valor, str) or not valor.strip():
        return False
    return clave not in _ENUMS or valor in _ENUMS[clave]


def validar_esperado(esperado: dict[str, Any], banco: Banco) -> None:
    """Como lo compara `comparar_modelos.py`: un valor, "*" (cualquiera), null (sin dato) o una
    lista de alternativas. Los enumerados se validan aquí y no en el modelo."""
    if not esperado:
        raise CasoInvalido("Indica al menos qué debería haber entendido el asistente")
    for clave, valor in esperado.items():
        if clave not in CLAVES_ESPERADO:
            raise CasoInvalido(f"Clave desconocida: {clave}")
        valores = valor if isinstance(valor, list) else [valor]
        if not valores or not all(_valor_valido(clave, v) for v in valores):
            raise CasoInvalido(f"Valor no válido para {clave}: {valor!r}")
    if banco == "clasificacion":
        if not isinstance(esperado.get("categoria"), str) or esperado["categoria"] == "*":
            raise CasoInvalido("Un caso de clasificación necesita la categoría")
        urgencias = esperado.get("urgencia")
        urgencias = urgencias if isinstance(urgencias, list) else [urgencias]
        if not all(u in URGENCIAS for u in urgencias):
            raise CasoInvalido("Un caso de clasificación necesita la urgencia (o varias válidas)")


def quitar_telefonos(texto: str) -> str:
    return _TELEFONO.sub(TELEFONO_FICTICIO, texto)


def seudonimizar(texto: str, nombre_real: str | None) -> str:
    """Cambia el nombre del vecino por uno ficticio, palabra a palabra, sin tocar el resto."""
    if not nombre_real:
        return texto
    partes = [p for p in nombre_real.split() if len(p) >= 2 and p.lower() not in _PARTICULAS]
    for i, parte in enumerate(partes):
        ficticio = NOMBRE_FICTICIO[min(i, len(NOMBRE_FICTICIO) - 1)]
        texto = re.sub(rf"\b{re.escape(parte)}\b", ficticio, texto, flags=re.IGNORECASE)
    return texto


def _limpiar(valor: Any, nombre_real: str | None) -> Any:
    if isinstance(valor, str):
        return quitar_telefonos(seudonimizar(valor, nombre_real))
    if isinstance(valor, list):
        return [_limpiar(v, nombre_real) for v in valor]
    return valor


def construir_caso(
    entrada: CasoBancoEntrada,
    turnos: list[dict[str, Any]],
    *,
    nombre_real: str | None,
    equipos: list[tuple[str, str]] | None,
) -> dict[str, Any]:
    """Campos del caso a partir de los turnos guardados de la llamada. Lanza `LookupError` si el
    turno no existe y `CasoInvalido` si no es un turno del vecino o lo esperado no vale."""
    validar_esperado(entrada.esperado, entrada.banco)
    turno = next((t for t in turnos if t["turno"] == entrada.turno), None)
    if turno is None:
        raise LookupError(f"La llamada no tiene turno {entrada.turno}")
    if not (turno.get("vecino") or "").strip():
        raise CasoInvalido("Ese turno no tiene nada dicho por el vecino")
    anterior = next((t for t in turnos if t["turno"] == entrada.turno - 1), None)
    mensajes = (anterior or {}).get("asistente") or []
    pregunta = entrada.pregunta or (mensajes[-1] if mensajes else None)
    nodo = turno.get("nodo_inicial") or "inicio"
    return {
        "turno": entrada.turno,
        "banco": entrada.banco,
        "nodo": nodo,
        "pregunta": _limpiar(pregunta, nombre_real),
        "oido": _limpiar(entrada.oido, nombre_real) if entrada.oido else None,
        "entrada": _limpiar(turno["vecino"], nombre_real),
        # El grafo interpreta siempre con el catálogo (una sola llamada clasifica también)
        "catalogo": True,
        "equipos": [list(e) for e in equipos] if equipos and nodo not in _NODOS_SIN_FINCA else None,
        "esperado": {k: _limpiar(v, nombre_real) for k, v in entrada.esperado.items()},
        "nota": _limpiar(entrada.nota, nombre_real),
    }
