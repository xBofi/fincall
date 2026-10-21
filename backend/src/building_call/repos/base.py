"""Contrato de acceso a datos. La lógica del grafo solo depende de este protocolo."""

from __future__ import annotations

from typing import Protocol

from building_call.domain.configuracion import ConfiguracionEfectiva
from building_call.domain.entidades import (
    Administrador,
    Equipo,
    Especialista,
    Finca,
    Incidencia,
    Llamada,
    Vivienda,
)


class Repositorio(Protocol):
    async def listar_fincas(self) -> list[Finca]: ...

    async def obtener_finca(self, finca_id: str) -> Finca | None: ...

    async def obtener_administrador(self, administrador_id: str) -> Administrador | None: ...

    async def viviendas_de(self, finca_id: str) -> list[Vivienda]: ...

    async def especialistas_de(self, administrador_id: str) -> list[Especialista]: ...

    async def equipos_de(self, finca_id: str) -> list[Equipo]: ...

    async def configuracion_efectiva(
        self, finca_id: str
    ) -> tuple[ConfiguracionEfectiva, str | None]:
        """Devuelve la configuración resuelta y el id de la versión de finca aplicada."""
        ...

    async def guardar_llamada(self, llamada: Llamada) -> None: ...

    async def guardar_incidencia(self, incidencia: Incidencia) -> None: ...

    async def listar_incidencias(self) -> list[Incidencia]: ...
