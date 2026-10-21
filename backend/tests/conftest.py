from __future__ import annotations

import re
from typing import Any

import pytest

from building_call.graph.nodos import Dependencias
from building_call.repos.memoria import RepositorioMemoria
from building_call.servicios import GestorLlamadas
from building_call.settings import settings

# Con largo real: por debajo del mínimo que exige la API es como no tener secreto
SECRETO_DE_PRUEBA = "secreto-de-prueba-con-largo-suficiente-0123456789"


@pytest.fixture(autouse=True)
def entorno_de_prueba(monkeypatch: pytest.MonkeyPatch) -> None:
    """La suite no depende del `.env` del desarrollador.

    Sin esto, un `BC_REPOSITORIO=postgres` en la máquina de quien programa cambia el
    comportamiento de tests que nada tienen que ver con la base de datos, y la CI —que no tiene
    `.env`— ejecuta algo distinto de lo que se probó en local. Un test que quiera el modo real lo
    dice explícitamente.
    """
    monkeypatch.setattr(settings, "repositorio", "memoria")
    monkeypatch.setattr(settings, "sesion_secreto", SECRETO_DE_PRUEBA)


def _clasificar(u: str, catalogo: list[str]) -> tuple[str, str]:
    if "agua" in u or "fuga" in u or "grifo" in u:
        cat, urg = "fontaneria", "alta" if "inund" in u else "media"
    elif "luz" in u or "enchufe" in u:
        cat, urg = "electricidad", "media"
    elif "ascensor" in u:
        cat, urg = "ascensor", "emergencia" if "dentro" in u else "alta"
    elif "limpieza" in u or "basura" in u:
        cat, urg = "limpieza", "baja"
    else:
        cat, urg = "no_clasificable", "media"
    assert cat in catalogo
    return cat, urg


class LLMFalso:
    """Simula la extracción estructurada con reglas simples. Despacha por `title` del esquema."""

    nombre = "falso/deterministico"

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, str]] = []

    async def disponible(self) -> bool:
        return True

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:
        titulo = esquema.get("title")
        self.llamadas.append((titulo or "", usuario))
        if titulo == "interpretacion":
            return self._interpretar(usuario, esquema)
        if titulo == "redaccion":
            m = re.search(r'MENSAJE OBLIGATORIO: """(.*)"""', usuario, re.DOTALL)
            return {"respuesta": "Vale. " + (m.group(1) if m else "")}
        if titulo == "clasificacion":
            catalogo = esquema["properties"]["categoria"]["enum"]
            cat, urg = _clasificar(usuario.lower(), catalogo)
            return {"categoria": cat, "urgencia": urg, "resumen": f"El vecino informa: {usuario}"}
        raise AssertionError(f"esquema desconocido: {titulo}")

    def _interpretar(self, usuario: str, esquema: dict[str, Any]) -> dict[str, Any]:
        # El mensaje de usuario trae la pregunta y la respuesta; interpretamos solo la respuesta
        m = re.search(r'Respuesta del vecino: """(.*)"""', usuario, re.DOTALL)
        texto = (m.group(1) if m else usuario).strip()
        u = texto.lower()
        salida: dict[str, Any] = {}
        if any(p in u for p in ("hablar con una persona", "con alguien", "una persona")):
            return {"intencion": "humano"}
        if any(p in u for p in ("adiós", "adios", "ya llamaré", "hasta luego")):
            return {"intencion": "colgar"}
        if any(p in u for p in ("no le he oído", "repita", "¿cómo?", "no he entendido")):
            return {"intencion": "repetir"}
        if "se está grabando" in u or "para qué necesita" in u:
            return {"intencion": "pregunta", "pregunta": texto}
        if "cuánto pago" in u or "cuota" in u:
            return {"intencion": "fuera_ambito"}
        if u.startswith(("sí", "si,", "si ", "eso es", "correcto")) or u in ("sí", "si"):
            salida["confirma"] = True
        elif u.startswith("no"):
            salida["confirma"] = False
        m_dir = re.search(
            r"(calle mayor \w+|avenida (?:de la )?alameda \w+|alameda \w+|mayor \w+)", u
        )
        if m_dir:
            salida["direccion"] = m_dir.group(1)
        m_portal = re.search(r"portal\s*(\w+)", u)
        resto = re.sub(r"portal\s*\w+", " ", u)
        pisos = re.findall(
            r"\b(\d+|primero|segundo|tercero|cuarto|bajo)\b\s*[ºo°]?\s*([a-d])\b", resto
        )
        m_piso = pisos[-1] if pisos else None  # una corrección menciona el dato bueno al final
        if m_portal:
            salida["portal"] = m_portal.group(1)
        if m_piso:
            salida["piso"], salida["puerta"] = m_piso[0], m_piso[1].upper()
        m_nom = re.search(r"(?:soy|me llamo)\s+([a-záéíóúñ]+(?:\s+(?!y\b)[a-záéíóúñ]+)?)", u)
        if m_nom:
            salida["nombre"] = " ".join(w.capitalize() for w in m_nom.group(1).split())
        elif (
            re.fullmatch(r"[a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?", u)
            and "confirma" not in salida
            and not m_dir
        ):
            salida["nombre"] = texto.title()
        palabras_problema = (
            "fuga",
            "agua",
            "luz",
            "ascensor",
            "basura",
            "ovni",
            "enchufe",
            "puerta del",
            "roto",
            "rota",
            "ruido",
            "parado",
        )
        if any(p in u for p in palabras_problema) and "problema" not in salida:
            salida["problema"] = texto
            if "categoria" in esquema["properties"]:
                cat, urg = _clasificar(u, esquema["properties"]["categoria"]["enum"])
                salida.update(categoria=cat, urgencia=urg, resumen=f"El vecino informa: {texto}")
        if "corregir" in u or "me he equivocado" in u or "antes le he dicho" in u:
            salida["intencion"] = "corregir"
        return salida


@pytest.fixture
def repo() -> RepositorioMemoria:
    # Los tests de flujo no piden el turno de detalle (configurable por administrador)
    r = RepositorioMemoria()
    r.config_administrador["adm-demo"] = r.config_administrador["adm-demo"].model_copy(
        update={"pedir_detalle": False}
    )
    return r


@pytest.fixture
def gestor_detalle(llm: LLMFalso) -> GestorLlamadas:
    return GestorLlamadas(Dependencias(repo=RepositorioMemoria(), llm=llm))


@pytest.fixture
def llm() -> LLMFalso:
    return LLMFalso()


@pytest.fixture
def deps(repo: RepositorioMemoria, llm: LLMFalso) -> Dependencias:
    return Dependencias(repo=repo, llm=llm)


def sesion_de_prueba(repo: RepositorioMemoria, rol: str = "owner") -> str:
    """Inserta un usuario y una sesión vigente en el repositorio en memoria; devuelve el token
    para la cookie `bc_sesion`. Síncrono a propósito: sirve también con `TestClient`."""
    from datetime import UTC, datetime, timedelta

    from building_call.auth import hash_token_sesion
    from building_call.domain.operaciones import Usuario
    from building_call.repos.operativo import SesionAcceso

    repo._operativo()
    usuario = Usuario(
        id=f"usr-{rol}",
        nombre_usuario=str(rol),
        rol=rol,
        activo=True,
        hash_contrasena="x",  # type: ignore[arg-type]
    )
    repo.usuarios[usuario.id] = usuario
    token = f"token-de-prueba-{rol}"
    ahora = datetime.now(UTC)
    repo.sesiones[hash_token_sesion(token)] = SesionAcceso(
        f"ses-{rol}", usuario.id, hash_token_sesion(token), ahora, ahora + timedelta(hours=1)
    )
    return token


@pytest.fixture
def gestor(deps: Dependencias) -> GestorLlamadas:
    return GestorLlamadas(deps)
