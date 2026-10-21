"""Repositorio en memoria con datos de demostración.

Permite validar los agentes sin PostgreSQL. No es el modo de producción.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from building_call.domain.configuracion import (
    ConfiguracionEfectiva,
    ConfiguracionParcial,
    Contacto,
    resolver,
)
from building_call.domain.entidades import (
    Administrador,
    Equipo,
    Especialista,
    Finca,
    Incidencia,
    Llamada,
    Vivienda,
)
from building_call.domain.operaciones import Usuario
from building_call.repos.operativo import SesionAcceso


def _viviendas(
    finca_id: str, portales: list[str | None], pisos: int, puertas: list[str]
) -> list[Vivienda]:
    out: list[Vivienda] = []
    for portal in portales:
        for piso in range(1, pisos + 1):
            for puerta in puertas:
                vid = f"{finca_id}-{portal or 'u'}-{piso}{puerta}"
                out.append(
                    Vivienda(
                        id=vid, finca_id=finca_id, portal=portal, piso=str(piso), puerta=puerta
                    )
                )
    return out


class RepositorioMemoria:
    def __init__(self) -> None:
        self.administradores = {
            "adm-demo": Administrador(id="adm-demo", nombre="Administraciones Demo SL"),
        }
        self.especialistas = {
            "esp-fontanero": Especialista(
                id="esp-fontanero",
                administrador_id="adm-demo",
                nombre="Fontanería Pérez",
                categoria_principal="fontaneria",
                canal_contacto="correo",
                destino="fontaneria@demo.test",
            ),
            "esp-electricista": Especialista(
                id="esp-electricista",
                administrador_id="adm-demo",
                nombre="Electricidad Gómez",
                categoria_principal="electricidad",
                canal_contacto="correo",
                destino="electricidad@demo.test",
            ),
            "esp-ascensores": Especialista(
                id="esp-ascensores",
                administrador_id="adm-demo",
                nombre="Ascensores Norte",
                categoria_principal="ascensor",
                canal_contacto="llamada",
                destino="+34600000001",
            ),
            "esp-cerrajero": Especialista(
                id="esp-cerrajero",
                administrador_id="adm-demo",
                nombre="Cerrajería Rápida",
                categoria_principal="cerrajeria",
                canal_contacto="correo",
                destino="cerrajeria@demo.test",
            ),
            # Mantenedores de equipos concretos (no son el especialista de ninguna categoría)
            "esp-porteros": Especialista(
                id="esp-porteros",
                administrador_id="adm-demo",
                nombre="Porteros Levante",
                categoria_principal="electricidad",
                canal_contacto="correo",
                destino="soporte@porteroslevante.demo.test",
            ),
            "esp-automatismos": Especialista(
                id="esp-automatismos",
                administrador_id="adm-demo",
                nombre="Automatismos Sur",
                categoria_principal="cerrajeria",
                canal_contacto="llamada",
                destino="+34600000002",
            ),
        }
        # Inventario de demostración, hasta que el administrador dé el real (Pendiente)
        self.equipos: dict[str, list[Equipo]] = {
            "finca-mayor-12": [
                Equipo(
                    id="eq-mayor12-portero",
                    finca_id="finca-mayor-12",
                    tipo="portero automático",
                    ubicacion="portal",
                    marca="Fermax",
                    modelo="Citymax",
                    instalador="Porteros Levante",
                    mantenedor_id="esp-porteros",
                    contrato="PL-2024-118",
                ),
                Equipo(
                    id="eq-mayor12-ascensor",
                    finca_id="finca-mayor-12",
                    tipo="ascensor",
                    marca="Orona",
                    instalador="Orona",
                    mantenedor_id="esp-ascensores",
                    contrato="AN-5531",
                ),
            ],
            "finca-alameda-5": [
                Equipo(
                    id="eq-alameda5-garaje",
                    finca_id="finca-alameda-5",
                    tipo="puerta de garaje automática",
                    ubicacion="garaje",
                    marca="Nice",
                    instalador="Automatismos Sur",
                    mantenedor_id="esp-automatismos",
                ),
            ],
        }
        self.fincas = {
            "finca-mayor-12": Finca(
                id="finca-mayor-12",
                administrador_id="adm-demo",
                direccion="Calle Mayor 12",
                correo="mayor12@demo.test",
                telefono_oficial="+34910000001",
            ),
            "finca-alameda-5": Finca(
                id="finca-alameda-5",
                administrador_id="adm-demo",
                direccion="Avenida de la Alameda 5",
                correo="alameda5@demo.test",
                telefono_oficial="+34910000002",
            ),
        }
        self.viviendas: dict[str, list[Vivienda]] = {
            "finca-mayor-12": _viviendas("finca-mayor-12", [None], 4, ["A", "B"]),
            "finca-alameda-5": _viviendas("finca-alameda-5", ["1", "2"], 3, ["A", "B", "C"]),
        }
        self.config_administrador: dict[str, ConfiguracionParcial] = {
            "adm-demo": ConfiguracionParcial(
                especialista_por_categoria={
                    "fontaneria": "esp-fontanero",
                    "electricidad": "esp-electricista",
                    "ascensor": "esp-ascensores",
                    "cerrajeria": "esp-cerrajero",
                },
            ),
        }
        # Versión de configuración por finca: (id_version, documento)
        self.config_finca: dict[str, tuple[str, ConfiguracionParcial]] = {
            "finca-mayor-12": (
                "cfg-mayor-12-v1",
                ConfiguracionParcial(
                    contacto=Contacto(correo="mayor12@demo.test", telefono_oficial="+34910000001"),
                ),
            ),
            "finca-alameda-5": (
                "cfg-alameda-5-v1",
                ConfiguracionParcial(
                    contacto=Contacto(correo="alameda5@demo.test", telefono_oficial="+34910000002"),
                    intentos_vivienda=4,
                ),
            ),
        }
        self.llamadas: dict[str, Llamada] = {}
        self.incidencias: dict[str, Incidencia] = {}

    async def listar_fincas(self) -> list[Finca]:
        return list(self.fincas.values())

    async def obtener_finca(self, finca_id: str) -> Finca | None:
        return self.fincas.get(finca_id)

    async def obtener_administrador(self, administrador_id: str) -> Administrador | None:
        return self.administradores.get(administrador_id)

    async def viviendas_de(self, finca_id: str) -> list[Vivienda]:
        return list(self.viviendas.get(finca_id, []))

    async def especialistas_de(self, administrador_id: str) -> list[Especialista]:
        return [e for e in self.especialistas.values() if e.administrador_id == administrador_id]

    async def equipos_de(self, finca_id: str) -> list[Equipo]:
        return list(self.equipos.get(finca_id, []))

    async def configuracion_efectiva(
        self, finca_id: str
    ) -> tuple[ConfiguracionEfectiva, str | None]:
        finca = self.fincas[finca_id]
        version_id, cfg_finca = self.config_finca.get(finca_id, (None, None))
        cfg_adm = self.config_administrador.get(finca.administrador_id)
        return resolver(cfg_adm, cfg_finca), version_id

    async def guardar_llamada(self, llamada: Llamada) -> None:
        self.llamadas[llamada.id] = llamada

    async def guardar_incidencia(self, incidencia: Incidencia) -> None:
        self.incidencias[incidencia.id] = incidencia

    async def listar_incidencias(self) -> list[Incidencia]:
        return list(self.incidencias.values())

    # ------------------------------------------------------------- operativo (acceso y trazabilidad)

    def _operativo(self) -> None:
        if not hasattr(self, "usuarios"):
            self.usuarios: dict[str, Usuario] = {}
            self.sesiones: dict[str, SesionAcceso] = {}
            self.turnos: dict[str, list[dict[str, Any]]] = {}
            self.decisiones: dict[str, list[dict[str, Any]]] = {}
            self.pistas: dict[str, dict[str, Any]] = {}
            self.auditoria: list[dict[str, Any]] = []
            self.plantillas: dict[str, dict[str, Any]] = {}
            self.plantilla_historial: list[dict[str, Any]] = []
            self.casos_banco: dict[str, dict[str, Any]] = {}

    async def guardar_usuario(self, usuario: Usuario) -> None:
        self._operativo()
        self.usuarios[usuario.id] = usuario

    async def obtener_usuario(self, usuario_id: str) -> Usuario | None:
        self._operativo()
        return self.usuarios.get(usuario_id)

    async def obtener_usuario_por_nombre(self, nombre_usuario: str) -> Usuario | None:
        self._operativo()
        return next((u for u in self.usuarios.values() if u.nombre_usuario == nombre_usuario), None)

    async def obtener_usuario_por_identidad(self, proveedor: str, sujeto: str) -> Usuario | None:
        self._operativo()
        return next(
            (u for u in self.usuarios.values() if (u.proveedor, u.sujeto) == (proveedor, sujeto)),
            None,
        )

    async def listar_usuarios(self) -> list[Usuario]:
        self._operativo()
        return list(self.usuarios.values())

    async def eliminar_usuario(self, usuario_id: str) -> None:
        self._operativo()
        self.usuarios.pop(usuario_id, None)
        self.sesiones = {h: s for h, s in self.sesiones.items() if s.usuario_id != usuario_id}

    async def revocar_sesiones_de(self, usuario_id: str, cuando: datetime) -> None:
        self._operativo()
        for s in self.sesiones.values():
            if s.usuario_id == usuario_id and s.revocada_en is None:
                s.revocada_en = cuando

    async def guardar_sesion(self, sesion: SesionAcceso) -> None:
        self._operativo()
        self.sesiones[sesion.token_hash] = sesion

    async def obtener_sesion_por_hash(self, token_hash: str) -> SesionAcceso | None:
        self._operativo()
        return self.sesiones.get(token_hash)

    async def revocar_sesion(self, token_hash: str, cuando: datetime) -> None:
        self._operativo()
        if token_hash in self.sesiones:
            self.sesiones[token_hash].revocada_en = cuando

    async def renovar_sesion(self, token_hash: str, expira_en: datetime) -> None:
        self._operativo()
        if token_hash in self.sesiones:
            self.sesiones[token_hash].expira_en = expira_en

    async def listar_llamadas(self, estado: str | None = None, limite: int = 100) -> list[Llamada]:
        filas = [ll for ll in self.llamadas.values() if estado is None or ll.estado == estado]
        filas.sort(key=lambda ll: ll.iniciada_en, reverse=True)
        return filas[:limite]

    async def obtener_llamada(self, llamada_id: str) -> Llamada | None:
        return self.llamadas.get(llamada_id)

    async def guardar_turno(self, llamada_id: str, turno: int, datos: dict[str, Any]) -> None:
        self._operativo()
        lista = self.turnos.setdefault(llamada_id, [])
        lista[:] = [t for t in lista if t["turno"] != turno]
        lista.append({"turno": turno, **datos})

    async def listar_turnos(self, llamada_id: str) -> list[dict[str, Any]]:
        self._operativo()
        return sorted(self.turnos.get(llamada_id, []), key=lambda t: t["turno"])

    async def guardar_decision(
        self, llamada_id: str, turno: int, nodo: str, datos: dict[str, Any]
    ) -> None:
        self._operativo()
        self.decisiones.setdefault(llamada_id, []).append({"turno": turno, "nodo": nodo, **datos})

    async def listar_decisiones(self, llamada_id: str) -> list[dict[str, Any]]:
        self._operativo()
        return list(self.decisiones.get(llamada_id, []))

    async def guardar_pista(self, pista: dict[str, Any]) -> None:
        self._operativo()
        self.pistas[pista["id"]] = dict(pista)

    async def listar_pistas(self, llamada_id: str) -> list[dict[str, Any]]:
        self._operativo()
        return [p for p in self.pistas.values() if p["llamada_id"] == llamada_id]

    async def obtener_pista(self, pista_id: str) -> dict[str, Any] | None:
        self._operativo()
        return self.pistas.get(pista_id)

    async def pistas_vencidas(self, ahora: datetime) -> list[dict[str, Any]]:
        self._operativo()
        return [
            p for p in self.pistas.values() if p["expira_en"] <= ahora and not p.get("eliminada_en")
        ]

    async def marcar_pista_eliminada(self, pista_id: str, cuando: datetime) -> None:
        self._operativo()
        if pista_id in self.pistas:
            self.pistas[pista_id]["eliminada_en"] = cuando

    async def registrar_auditoria(
        self, usuario_id: str | None, accion: str, recurso: str, detalle: dict[str, Any]
    ) -> None:
        self._operativo()
        # Como la clave foránea de Postgres: un evento solo puede citar a un usuario que existe
        if usuario_id is not None and usuario_id not in self.usuarios:
            raise KeyError(f"auditoría de un usuario inexistente: {usuario_id}")
        self.auditoria.append(
            {
                "usuario_id": usuario_id,
                "accion": accion,
                "recurso": recurso,
                "detalle": detalle,
                "ocurrido_en": datetime.now(UTC).isoformat(),
            }
        )

    async def listar_auditoria(self, limite: int = 200) -> list[dict[str, Any]]:
        self._operativo()
        return list(reversed(self.auditoria))[:limite]

    # ----------------------------------------------------------------- plantillas del asistente

    async def listar_plantillas_sobrescritas(self) -> dict[str, tuple[str, int]]:
        self._operativo()
        return {k: (v["texto"], v["version"]) for k, v in self.plantillas.items()}

    async def guardar_plantilla(self, plantilla_id: str, texto: str, autor: str | None) -> int:
        self._operativo()
        version = self.plantillas.get(plantilla_id, {}).get("version", 0) + 1
        self.plantillas[plantilla_id] = {"texto": texto, "version": version, "autor": autor}
        self.plantilla_historial.append(
            {
                "plantilla_id": plantilla_id,
                "version": version,
                "texto": texto,
                "autor": autor,
                "creada_en": datetime.now(UTC).isoformat(),
            }
        )
        return version

    async def borrar_plantilla(self, plantilla_id: str) -> None:
        self._operativo()
        self.plantillas.pop(plantilla_id, None)

    async def historial_plantilla(self, plantilla_id: str) -> list[dict[str, Any]]:
        self._operativo()
        return [h for h in reversed(self.plantilla_historial) if h["plantilla_id"] == plantilla_id]

    # ----------------------------------------------------------------- casos del banco de pruebas

    async def guardar_caso_banco(self, caso: dict[str, Any]) -> dict[str, Any]:
        self._operativo()
        ahora = datetime.now(UTC).isoformat()
        clave = (caso["llamada_id"], caso["turno"], caso["banco"])
        previo = next(
            (
                c
                for c in self.casos_banco.values()
                if (c["llamada_id"], c["turno"], c["banco"]) == clave
            ),
            None,
        )
        guardado = {
            **caso,
            "id": previo["id"] if previo else f"cb-{uuid4().hex[:16]}",
            "creado_en": previo["creado_en"] if previo else ahora,
            "actualizado_en": ahora,
        }
        self.casos_banco[guardado["id"]] = guardado
        return dict(guardado)

    async def listar_casos_banco(self, llamada_id: str | None = None) -> list[dict[str, Any]]:
        self._operativo()
        return [
            dict(c)
            for c in sorted(self.casos_banco.values(), key=lambda c: (c["creado_en"], c["id"]))
            if llamada_id is None or c["llamada_id"] == llamada_id
        ]

    async def eliminar_caso_banco(self, caso_id: str) -> dict[str, Any] | None:
        self._operativo()
        return self.casos_banco.pop(caso_id, None)
