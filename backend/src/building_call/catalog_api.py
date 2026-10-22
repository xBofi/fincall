"""API del catálogo: fincas, viviendas, profesionales, equipos y quién atiende cada categoría.

Es exactamente lo que el asistente consulta en cada llamada: con qué direcciones reconoce una
finca, qué viviendas acepta, a qué profesional avisa y qué equipo tiene mantenedor propio.

- **Lectura**: cualquier usuario de la consola.
- **Escritura**: `owner` y `administrator`. Cada cambio queda auditado con el antes y el después.
- **Ids**: los genera el servidor; los que mande el cliente se ignoran.
- **Configuración**: quién atiende cada categoría y el contacto de la finca viven en capas
  versionadas. Editar crea una versión nueva; la anterior se conserva.
- **Borrar**: no se borra nada que tenga historial (llamadas o incidencias), y un profesional
  que el asistente usa solo se borra si se pide expresamente (`force`), diciendo qué se
  desasigna.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Literal, Self
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from building_call.access import Access
from building_call.domain.config import (
    Contact,
    PartialConfig,
    resolve,
)
from building_call.domain.entities import (
    Administrator,
    Equipment,
    Estate,
    Home,
    Specialist,
)
from building_call.domain.operations import Role, User
from building_call.repos.base import Repository
from building_call.repos.catalog import CatalogRepository
from building_call.repos.operational import OperationalRepository

READERS = (Role.owner, Role.administrator, Role.quality)
EDITORS = (Role.owner, Role.administrator)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?\d{6,15}$")


def _normalize(text: str) -> str:
    """'Calle  Mayor 12' y 'calle mayor 12' son la misma finca para el asistente."""
    unaccented = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", unaccented).strip().lower()


def _new_id(prefix: str, text: str | None = None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _normalize(text or "")).strip("-")[:32].strip("-")
    suffix = uuid4().hex[:6]
    return f"{prefix}-{slug}-{suffix}" if slug else f"{prefix}-{uuid4().hex[:12]}"


def _phone(value: str) -> str:
    clean = re.sub(r"[\s.-]", "", value)
    if not _PHONE.fullmatch(clean):
        raise ValueError("teléfono no válido: solo dígitos, con prefijo + opcional")
    return clean


def _optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# ----------------------------------------------------------------------------- cuerpos de entrada


class EstateInput(BaseModel):
    address: str = Field(min_length=3, max_length=300)
    email: str = Field(max_length=200)
    official_phone: str = Field(max_length=32)

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v).strip()

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL.fullmatch(v):
            raise ValueError("correo no válido")
        return v

    @field_validator("official_phone")
    @classmethod
    def _tel(cls, v: str) -> str:
        return _phone(v)


class NewEstate(EstateInput):
    administrator_id: str = Field(min_length=1, max_length=64)


class HomeInput(BaseModel):
    entrance: str | None = Field(default=None, max_length=16)
    floor: str = Field(min_length=1, max_length=16)
    door: str = Field(min_length=1, max_length=16)

    @field_validator("entrance")
    @classmethod
    def _entrance(cls, v: str | None) -> str | None:
        return _optional(v)

    @field_validator("floor")
    @classmethod
    def _floor(cls, v: str) -> str:
        v = v.strip().rstrip("ºª°").strip()
        if not v:
            raise ValueError("falta el piso")
        return v

    @field_validator("door")
    @classmethod
    def _door(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("falta la puerta")
        return v


class EquipmentInput(BaseModel):
    kind: str = Field(min_length=2, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    installer: str | None = Field(default=None, max_length=200)
    maintainer_id: str | None = Field(default=None, max_length=64)
    contract: str | None = Field(default=None, max_length=120)
    warranty_until: date | None = None

    @field_validator("kind")
    @classmethod
    def _type(cls, v: str) -> str:
        return v.strip()

    @field_validator("location", "brand", "model", "installer", "maintainer_id", "contract")
    @classmethod
    def _stripped(cls, v: str | None) -> str | None:
        return _optional(v)


class SpecialistInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    main_category: str = Field(min_length=1, max_length=64)
    contact_channel: Literal["email", "call"]
    target: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _destination_by_channel(self) -> Self:
        self.name = self.name.strip()
        target = self.target.strip()
        if self.contact_channel == "email":
            if not _EMAIL.fullmatch(target):
                raise ValueError("con canal correo, el destino debe ser un correo")
            self.target = target
        else:
            try:
                self.target = _phone(target)
            except ValueError as e:
                raise ValueError("con canal llamada, el destino debe ser un teléfono") from e
        return self


class NewSpecialist(SpecialistInput):
    administrator_id: str = Field(min_length=1, max_length=64)


class Assignment(BaseModel):
    specialist_id: str | None = None


# ------------------------------------------------------------------------------------- router


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def create_catalog_router(
    business: Repository,
    catalog: CatalogRepository,
    operational: OperationalRepository,
    access: Access,
) -> APIRouter:
    r = APIRouter(prefix="/api/catalogs")
    reader = access.requires(*READERS)
    editor = access.requires(*EDITORS)

    async def record_audit(user: User, action: str, resource: str, **detail: Any) -> None:
        await operational.record_audit(user.id, action, resource, detail)

    # --------------------------------------------------------------------------- ayudas comunes

    async def _estate_or_404(estate_id: str) -> Estate:
        f = await business.get_estate(estate_id)
        if f is None:
            raise HTTPException(404, "Finca desconocida")
        return f

    async def _admin_or_404(admin_id: str) -> Administrator:
        a = await business.get_administrator(admin_id)
        if a is None:
            raise HTTPException(404, "Administrador desconocido")
        return a

    async def _specialist_or_404(specialist_id: str) -> Specialist:
        e = await catalog.get_specialist(specialist_id)
        if e is None:
            raise HTTPException(404, "Profesional desconocido")
        return e

    async def _catalog_of(admin_id: str) -> list[str]:
        layer, _ = await catalog.config_of("administrator", admin_id)
        return resolve(layer).category_catalog

    def _specialist_view(e: Specialist, admin: Administrator | None) -> dict[str, Any]:
        return {
            **e.model_dump(),
            "administrator": admin.name if admin else e.administrator_id,
        }

    def _equipment_view(e: Equipment) -> dict[str, Any]:
        return {**e.model_dump(mode="json"), "description": e.description()}

    async def _estate_detail(f: Estate) -> dict[str, Any]:
        admin = await business.get_administrator(f.administrator_id)
        homes = await business.homes_of(f.id)
        cfg, cfg_id = await business.effective_config(f.id)
        layer_e, _ = await catalog.config_of("estate", f.id)
        return {
            "id": f.id,
            "address": f.address,
            "administrator_id": f.administrator_id,
            "administrator": admin.name if admin else f.administrator_id,
            "email": f.email,
            "official_phone": f.official_phone,
            "homes": [
                {"id": v.id, "entrance": v.entrance, "floor": v.floor, "door": v.door}
                for v in homes
            ],
            # Equipos con quien los mantiene: se avisa a su mantenedor antes que al especialista
            "equipment_list": [_equipment_view(e) for e in await business.equipment_of(f.id)],
            # Las tres capas ya resueltas: la forma más rápida de entender por qué una finca se
            # comporta distinto de otra
            "effective_config": cfg.model_dump(),
            "config_id": cfg_id,
            # La finca fija sus propios profesionales: lo que se asigne al administrador no le llega
            "own_assignment": bool(layer_e and layer_e.specialist_by_category is not None),
        }

    async def _administrator_view(admin: Administrator) -> dict[str, Any]:
        estates = [f for f in await business.list_estates() if f.administrator_id == admin.id]
        layer, cfg_id = await catalog.config_of("administrator", admin.id)
        resolved = resolve(layer)
        return {
            "id": admin.id,
            "name": admin.name,
            "estates": [{"id": f.id, "address": f.address} for f in estates],
            "specialists": [
                {
                    "id": e.id,
                    "name": e.name,
                    "main_category": e.main_category,
                    "contact_channel": e.contact_channel,
                    "target": e.target,
                }
                for e in await business.specialists_of(admin.id)
            ],
            # Capa del administrador: la heredan sus fincas salvo que una la sobrescriba
            "category_catalog": resolved.category_catalog,
            "specialist_by_category": resolved.specialist_by_category,
            "config_id": cfg_id,
        }

    async def _address_free(address: str, salvo: str | None = None) -> None:
        key = _normalize(address)
        for f in await business.list_estates():
            if f.id != salvo and _normalize(f.address) == key:
                raise HTTPException(
                    409,
                    f"Ya existe una finca en «{f.address}»: el asistente no sabría cuál de "
                    "las dos es la del vecino.",
                )

    async def _home_free(estate_id: str, v: HomeInput, salvo: str | None = None) -> None:
        key = (_normalize(v.entrance or ""), _normalize(v.floor), _normalize(v.door))
        for x in await business.homes_of(estate_id):
            if x.id == salvo:
                continue
            if (_normalize(x.entrance or ""), _normalize(x.floor), _normalize(x.door)) == key:
                raise HTTPException(409, f"La vivienda {x.label()} ya existe en esta finca.")

    async def _home_or_404(estate_id: str, home_id: str) -> Home:
        v = next((x for x in await business.homes_of(estate_id) if x.id == home_id), None)
        if v is None:
            raise HTTPException(404, "Vivienda desconocida en esta finca")
        return v

    async def _equipment_or_404(estate_id: str, equipment_id: str) -> Equipment:
        e = next((x for x in await business.equipment_of(estate_id) if x.id == equipment_id), None)
        if e is None:
            raise HTTPException(404, "Equipo desconocido en esta finca")
        return e

    async def _valid_maintainer(estate: Estate, maintainer_id: str | None) -> None:
        if maintainer_id is None:
            return
        e = await catalog.get_specialist(maintainer_id)
        if e is None or e.administrator_id != estate.administrator_id:
            raise HTTPException(
                422,
                "El mantenedor debe ser un profesional del mismo administrador que la finca: "
                "el asistente le avisará por su canal.",
            )

    async def _without_history(field: Any, value: str, what: str) -> None:
        refs = await catalog.count_references(field, value)
        parts = [
            _plural(n, singular, plural)
            for n, singular, plural in (
                (refs["calls"], "llamada", "llamadas"),
                (refs["incidents"], "incidencia", "incidencias"),
            )
            if n
        ]
        if parts:
            raise HTTPException(
                409,
                f"No se puede borrar {what}: tiene {' y '.join(parts)} en el historial, que "
                "se quedaría sin saber a qué se refería.",
            )

    async def _sync_contact(f: Estate) -> str | None:
        """El asistente avisa al contacto de la capa de finca: se mantiene igual que la ficha."""
        layer, version = await catalog.config_of("estate", f.id)
        contact = Contact(email=f.email, official_phone=f.official_phone)
        if layer is not None and layer.contact == contact:
            return version
        updated = (layer or PartialConfig()).model_copy(update={"contact": contact})
        return await catalog.save_config("estate", f.id, updated)

    # -------------------------------------------------------------------------------- lectura

    @r.get("/administrators")
    async def administrators(_: User = Depends(reader)) -> list[dict[str, Any]]:
        return [await _administrator_view(a) for a in await catalog.list_administrators()]

    @r.get("/estates")
    async def estates(_: User = Depends(reader)) -> list[dict[str, Any]]:
        output = []
        for f in await business.list_estates():
            admin = await business.get_administrator(f.administrator_id)
            homes = await business.homes_of(f.id)
            output.append(
                {
                    "id": f.id,
                    "address": f.address,
                    "administrator_id": f.administrator_id,
                    "administrator": admin.name if admin else f.administrator_id,
                    "email": f.email,
                    "official_phone": f.official_phone,
                    "homes": len(homes),
                }
            )
        return output

    @r.get("/estates/{estate_id}")
    async def estate(estate_id: str, _: User = Depends(reader)) -> dict[str, Any]:
        return await _estate_detail(await _estate_or_404(estate_id))

    @r.get("/specialists")
    async def specialists(_: User = Depends(reader)) -> list[dict[str, Any]]:
        output = []
        for admin in await catalog.list_administrators():
            for e in await business.specialists_of(admin.id):
                output.append(_specialist_view(e, admin))
        return output

    # --------------------------------------------------------------------------------- fincas

    @r.post("/estates", status_code=201)
    async def create_estate(body: NewEstate, actor: User = Depends(editor)) -> dict[str, Any]:
        if await business.get_administrator(body.administrator_id) is None:
            raise HTTPException(422, "Administrador desconocido")
        await _address_free(body.address)
        f = Estate(id=_new_id("estate", body.address), **body.model_dump())
        await catalog.save_estate(f)
        version = await _sync_contact(f)
        await record_audit(actor, "estate_created", f.id, after=f.model_dump(), config_id=version)
        return await _estate_detail(f)

    @r.put("/estates/{estate_id}")
    async def edit_estate(
        estate_id: str, body: EstateInput, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        before = await _estate_or_404(estate_id)
        await _address_free(body.address, salvo=estate_id)
        f = before.model_copy(update=body.model_dump())
        await catalog.save_estate(f)
        version = await _sync_contact(f)
        await record_audit(
            actor,
            "estate_edited",
            f.id,
            before=before.model_dump(),
            after=f.model_dump(),
            config_id=version,
        )
        return await _estate_detail(f)

    @r.delete("/estates/{estate_id}")
    async def delete_estate(estate_id: str, actor: User = Depends(editor)) -> dict[str, Any]:
        f = await _estate_or_404(estate_id)
        await _without_history("estate_id", estate_id, f"la finca «{f.address}»")
        homes = len(await business.homes_of(estate_id))
        equipment_list = len(await business.equipment_of(estate_id))
        await catalog.delete_estate(estate_id)
        await record_audit(
            actor,
            "estate_deleted",
            estate_id,
            before=f.model_dump(),
            homes=homes,
            equipment_list=equipment_list,
        )
        return {"deleted": estate_id}

    # ------------------------------------------------------------------------------- viviendas

    @r.post("/estates/{estate_id}/homes", status_code=201)
    async def create_home(
        estate_id: str, body: HomeInput, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        await _estate_or_404(estate_id)
        await _home_free(estate_id, body)
        v = Home(id=_new_id("home"), estate_id=estate_id, **body.model_dump())
        await catalog.save_home(v)
        await record_audit(actor, "home_created", v.id, estate_id=estate_id, after=v.model_dump())
        return v.model_dump()

    @r.put("/estates/{estate_id}/homes/{home_id}")
    async def edit_home(
        estate_id: str,
        home_id: str,
        body: HomeInput,
        actor: User = Depends(editor),
    ) -> dict[str, Any]:
        await _estate_or_404(estate_id)
        before = await _home_or_404(estate_id, home_id)
        await _home_free(estate_id, body, salvo=home_id)
        v = before.model_copy(update=body.model_dump())
        await catalog.save_home(v)
        await record_audit(
            actor,
            "home_edited",
            v.id,
            estate_id=estate_id,
            before=before.model_dump(),
            after=v.model_dump(),
        )
        return v.model_dump()

    @r.delete("/estates/{estate_id}/homes/{home_id}")
    async def delete_home(
        estate_id: str, home_id: str, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        await _estate_or_404(estate_id)
        v = await _home_or_404(estate_id, home_id)
        await _without_history("home_id", home_id, f"la vivienda {v.label()}")
        await catalog.delete_home(home_id)
        await record_audit(
            actor, "home_deleted", home_id, estate_id=estate_id, before=v.model_dump()
        )
        return {"deleted": home_id}

    # --------------------------------------------------------------------------------- equipos

    @r.post("/estates/{estate_id}/equipment", status_code=201)
    async def create_equipment(
        estate_id: str, body: EquipmentInput, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        f = await _estate_or_404(estate_id)
        await _valid_maintainer(f, body.maintainer_id)
        e = Equipment(id=_new_id("eq", body.kind), estate_id=estate_id, **body.model_dump())
        await catalog.save_equipment(e)
        await record_audit(
            actor, "equipment_created", e.id, estate_id=estate_id, after=e.model_dump(mode="json")
        )
        return _equipment_view(e)

    @r.put("/estates/{estate_id}/equipment/{equipment_id}")
    async def edit_equipment(
        estate_id: str, equipment_id: str, body: EquipmentInput, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        f = await _estate_or_404(estate_id)
        before = await _equipment_or_404(estate_id, equipment_id)
        await _valid_maintainer(f, body.maintainer_id)
        e = before.model_copy(update=body.model_dump())
        await catalog.save_equipment(e)
        await record_audit(
            actor,
            "equipment_edited",
            e.id,
            estate_id=estate_id,
            before=before.model_dump(mode="json"),
            after=e.model_dump(mode="json"),
        )
        return _equipment_view(e)

    @r.delete("/estates/{estate_id}/equipment/{equipment_id}")
    async def delete_equipment(
        estate_id: str, equipment_id: str, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        await _estate_or_404(estate_id)
        e = await _equipment_or_404(estate_id, equipment_id)
        await _without_history("equipment_id", equipment_id, f"el equipo «{e.description()}»")
        await catalog.delete_equipment(equipment_id)
        await record_audit(
            actor, "equipment_deleted", equipment_id, estate_id=estate_id, before=e.model_dump(mode="json")
        )
        return {"deleted": equipment_id}

    # --------------------------------------------------------------------------- profesionales

    async def _valid_category(admin_id: str, category: str) -> None:
        if category not in await _catalog_of(admin_id):
            raise HTTPException(422, f"La categoría «{category}» no está en el catálogo")

    @r.post("/specialists", status_code=201)
    async def create_specialist(
        body: NewSpecialist, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        admin = await business.get_administrator(body.administrator_id)
        if admin is None:
            raise HTTPException(422, "Administrador desconocido")
        await _valid_category(admin.id, body.main_category)
        e = Specialist(id=_new_id("spec", body.name), **body.model_dump())
        await catalog.save_specialist(e)
        await record_audit(actor, "specialist_created", e.id, after=e.model_dump())
        return _specialist_view(e, admin)

    @r.put("/specialists/{specialist_id}")
    async def edit_specialist(
        specialist_id: str, body: SpecialistInput, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        before = await _specialist_or_404(specialist_id)
        await _valid_category(before.administrator_id, body.main_category)
        # El administrador no cambia: sus asignaciones y equipos son de ese administrador
        data = body.model_dump(exclude={"administrator_id"})
        e = before.model_copy(update=data)
        await catalog.save_specialist(e)
        await record_audit(
            actor, "specialist_edited", e.id, before=before.model_dump(), after=e.model_dump()
        )
        return _specialist_view(e, await business.get_administrator(e.administrator_id))

    async def _uses_of(e: Specialist) -> tuple[list[tuple[str, str, str]], list[Equipment]]:
        """Dónde lo usa el asistente: (ámbito, id del ámbito, categoría) y equipos que mantiene."""
        categories: list[tuple[str, str, str]] = []
        layer, _ = await catalog.config_of("administrator", e.administrator_id)
        for cat, spec in ((layer.specialist_by_category or {}) if layer else {}).items():
            if spec == e.id:
                categories.append(("administrator", e.administrator_id, cat))
        equipment_list: list[Equipment] = []
        for f in await business.list_estates():
            if f.administrator_id != e.administrator_id:
                continue
            layer_e, _ = await catalog.config_of("estate", f.id)
            for cat, spec in ((layer_e.specialist_by_category or {}) if layer_e else {}).items():
                if spec == e.id:
                    categories.append(("estate", f.id, cat))
            equipment_list += [q for q in await business.equipment_of(f.id) if q.maintainer_id == e.id]
        return categories, equipment_list

    @r.delete("/specialists/{specialist_id}")
    async def delete_specialist(
        specialist_id: str, force: bool = False, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        e = await _specialist_or_404(specialist_id)
        await _without_history("specialist_id", specialist_id, f"a {e.name}")
        categories, equipment_list = await _uses_of(e)
        if (categories or equipment_list) and not force:
            parts = []
            if categories:
                names = sorted({c for _, _, c in categories})
                parts.append("atiende " + ", ".join(names))
            if equipment_list:
                parts.append("mantiene " + ", ".join(q.description() for q in equipment_list))
            raise HTTPException(
                409,
                f"{e.name} no se ha borrado porque el asistente lo usa: "
                f"{'; '.join(parts)}. Si se borra, esas averías quedarán para el "
                "administrador y los equipos sin mantenedor.",
            )

        versions: list[str] = []
        for scope, scope_id in dict.fromkeys((a, i) for a, i, _ in categories):
            layer, _ = await catalog.config_of(scope, scope_id)  # type: ignore[arg-type]
            assert layer is not None
            mapping = {c: x for c, x in (layer.specialist_by_category or {}).items() if x != e.id}
            updated = layer.model_copy(update={"specialist_by_category": mapping})
            versions.append(
                await catalog.save_config(scope, scope_id, updated)  # type: ignore[arg-type]
            )
        for q in equipment_list:
            await catalog.save_equipment(q.model_copy(update={"maintainer_id": None}))
        await catalog.delete_specialist(specialist_id)

        unassigned = sorted({c for _, _, c in categories})
        without_maintainer = [q.id for q in equipment_list]
        await record_audit(
            actor,
            "specialist_deleted",
            specialist_id,
            before=e.model_dump(),
            unassigned_categories=unassigned,
            equipment_without_maintainer=without_maintainer,
            configs=versions,
        )
        return {
            "deleted": specialist_id,
            "unassigned_categories": unassigned,
            "equipment_without_maintainer": without_maintainer,
        }

    # ------------------------------------------------------------ quién atiende cada categoría

    async def _consistent_estates(admin_id: str, updated: PartialConfig) -> None:
        """Cada finca hereda la capa nueva: ninguna puede quedar con una configuración que el
        asistente no sepa resolver (p. ej. una finca que acota su catálogo de categorías)."""
        broken = []
        for f in await business.list_estates():
            if f.administrator_id != admin_id:
                continue
            layer_e, _ = await catalog.config_of("estate", f.id)
            try:
                resolve(updated, layer_e)
            except ValidationError:
                broken.append(f.address)
        if broken:
            raise HTTPException(
                409,
                "No se ha guardado: "
                + ", ".join(f"«{d}»" for d in broken)
                + " no tiene esa categoría en su catálogo y el asistente no podría atender "
                "sus llamadas.",
            )

    @r.put("/administrators/{admin_id}/categories/{category}")
    async def assign_category(
        admin_id: str, category: str, body: Assignment, actor: User = Depends(editor)
    ) -> dict[str, Any]:
        admin = await _admin_or_404(admin_id)
        await _valid_category(admin_id, category)
        if body.specialist_id is not None:
            e = await catalog.get_specialist(body.specialist_id)
            if e is None or e.administrator_id != admin_id:
                raise HTTPException(422, "El profesional no es de este administrador")

        layer, _ = await catalog.config_of("administrator", admin_id)
        layer = layer or PartialConfig()
        mapping = dict(layer.specialist_by_category or {})
        previous = mapping.get(category)
        if body.specialist_id is None:
            mapping.pop(category, None)
        else:
            mapping[category] = body.specialist_id
        if previous != body.specialist_id:
            updated = layer.model_copy(update={"specialist_by_category": mapping})
            await _consistent_estates(admin_id, updated)
            version = await catalog.save_config("administrator", admin_id, updated)
            await record_audit(
                actor,
                "category_assigned",
                admin_id,
                category=category,
                before=previous,
                after=body.specialist_id,
                config_id=version,
            )
        return await _administrator_view(admin)

    return r
