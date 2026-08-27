"""Configuración de negocio por capas: defaults -> administrador -> finca.

Ver docs/superpowers/specs/2026-08-27-finca-configuration-and-graph-registry-design.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Urgencia = Literal["baja", "media", "alta", "emergencia"]
Canal = Literal["correo", "llamada", "ticket"]

URGENCIAS: tuple[Urgencia, ...] = ("baja", "media", "alta", "emergencia")
CATEGORIA_NO_CLASIFICABLE = "no_clasificable"


class ReglaAviso(BaseModel):
    urgencia: Urgencia
    canal: Canal
    # id de especialista, "administrador" o "coordinador"
    destino: str


class Contacto(BaseModel):
    correo: str
    telefono_oficial: str


class ConfiguracionParcial(BaseModel):
    """Capa de administrador o de finca: todo opcional, lo ausente se hereda."""

    grafo: str | None = None
    catalogo_categorias: list[str] | None = None
    especialista_por_categoria: dict[str, str] | None = None
    reglas_aviso: list[ReglaAviso] | None = None
    contacto: Contacto | None = None
    intentos_finca: int | None = Field(default=None, ge=1)
    intentos_vivienda: int | None = Field(default=None, ge=1)
    emergencias_activas: bool | None = None


class ConfiguracionEfectiva(BaseModel):
    """Resultado de resolver las tres capas. Se inyecta en el estado del grafo."""

    grafo: str = "estandar"
    catalogo_categorias: list[str]
    especialista_por_categoria: dict[str, str] = Field(default_factory=dict)
    reglas_aviso: list[ReglaAviso] = Field(default_factory=list)
    contacto: Contacto | None = None
    intentos_finca: int = 3
    intentos_vivienda: int = 3
    emergencias_activas: bool = False

    @model_validator(mode="after")
    def _categorias_coherentes(self) -> ConfiguracionEfectiva:
        desconocidas = set(self.especialista_por_categoria) - set(self.catalogo_categorias)
        if desconocidas:
            raise ValueError(
                f"especialista_por_categoria referencia categorías fuera del catálogo: "
                f"{sorted(desconocidas)}"
            )
        return self

    def urgencia_efectiva(self, urgencia: Urgencia) -> Urgencia:
        """Mientras las emergencias estén desactivadas se tratan como `alta`."""
        if urgencia == "emergencia" and not self.emergencias_activas:
            return "alta"
        return urgencia

    def regla_para(self, urgencia: Urgencia) -> ReglaAviso | None:
        urgencia = self.urgencia_efectiva(urgencia)
        return next((r for r in self.reglas_aviso if r.urgencia == urgencia), None)


DEFAULTS = ConfiguracionEfectiva(
    catalogo_categorias=[
        "fontaneria",
        "electricidad",
        "ascensor",
        "limpieza",
        "cerrajeria",
        "albanileria",
        "administrativo",
    ],
    reglas_aviso=[
        ReglaAviso(urgencia="baja", canal="ticket", destino="especialista"),
        ReglaAviso(urgencia="media", canal="ticket", destino="especialista"),
        ReglaAviso(urgencia="alta", canal="correo", destino="coordinador"),
    ],
)


def resolver(
    administrador: ConfiguracionParcial | None = None,
    finca: ConfiguracionParcial | None = None,
    defaults: ConfiguracionEfectiva = DEFAULTS,
) -> ConfiguracionEfectiva:
    """Fusiona las capas: cada campo declarado en una capa inferior sobrescribe al superior."""
    datos = defaults.model_dump()
    for capa in (administrador, finca):
        if capa is None:
            continue
        datos.update(capa.model_dump(exclude_none=True))
    return ConfiguracionEfectiva.model_validate(datos)
