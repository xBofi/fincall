"""Contrato de escritura del catálogo: fincas, viviendas, profesionales, equipos y la capa de
configuración que decide quién atiende cada categoría.

Complementa a `Repositorio` (lectura de negocio). Lo implementan `RepositorioMemoria` y
`RepositorioPostgres`. Las reglas de dominio (qué se puede borrar, quién puede mantener un
equipo) viven en `catalogo_api.py`; el repositorio solo guarda y cuenta.
"""

from __future__ import annotations

from typing import Literal, Protocol

from building_call.domain.configuracion import ConfiguracionParcial
from building_call.domain.entidades import (
    Administrador,
    Equipo,
    Especialista,
    Finca,
    Vivienda,
)

Ambito = Literal["administrador", "finca"]

#: Columnas por las que una llamada o una incidencia citan al catálogo
CampoReferencia = Literal["finca_id", "vivienda_id", "especialista_id", "equipo_id"]


class RepositorioCatalogo(Protocol):
    async def listar_administradores(self) -> list[Administrador]: ...

    async def obtener_especialista(self, especialista_id: str) -> Especialista | None: ...

    async def guardar_finca(self, finca: Finca) -> None: ...

    async def eliminar_finca(self, finca_id: str) -> None:
        """Borra la finca con sus viviendas, equipos y capas de configuración."""
        ...

    async def guardar_vivienda(self, vivienda: Vivienda) -> None: ...

    async def eliminar_vivienda(self, vivienda_id: str) -> None: ...

    async def guardar_especialista(self, especialista: Especialista) -> None: ...

    async def eliminar_especialista(self, especialista_id: str) -> None: ...

    async def guardar_equipo(self, equipo: Equipo) -> None: ...

    async def eliminar_equipo(self, equipo_id: str) -> None: ...

    async def configuracion_de(
        self, ambito: Ambito, ambito_id: str
    ) -> tuple[ConfiguracionParcial | None, str | None]:
        """La capa vigente (sin resolver) y el id de su versión."""
        ...

    async def guardar_configuracion(
        self, ambito: Ambito, ambito_id: str, capa: ConfiguracionParcial
    ) -> str:
        """Crea una versión nueva de la capa; la anterior se conserva. Devuelve su id."""
        ...

    async def contar_referencias(self, campo: CampoReferencia, valor: str) -> dict[str, int]:
        """Cuántas llamadas e incidencias citan ese dato: {'llamadas': n, 'incidencias': n}."""
        ...
