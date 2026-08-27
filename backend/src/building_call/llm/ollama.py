"""Cliente Ollama local con salida JSON.

Nota operativa (Ollama 0.23.x + qwen3.5): `format` con JSON Schema y `think=false` deja la
petición colgada hasta el timeout, y con `think=true` el modelo razona decenas de miles de
tokens. `format="json"` + `think=false` responde en ~1 s. El esquema se usa aquí solo para
describir las claves en el prompt y validar la respuesta en código.

Latencia: el prompt de sistema va primero y es estable por tarea para que Ollama reutilice la
caché KV del prefijo; el contexto variable va al final. `num_predict` acota la salida.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

_CERCA_CODIGO = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMError(RuntimeError):
    pass


def _extraer_json(contenido: str) -> dict[str, Any]:
    texto = contenido.strip()
    m = _CERCA_CODIGO.match(texto)
    if m:
        texto = m.group(1)
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        inicio, fin = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fin == -1:
            raise LLMError(f"El modelo no devolvió JSON: {contenido[:200]!r}") from None
        try:
            datos = json.loads(texto[inicio : fin + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON inválido del modelo: {e}") from e
    if not isinstance(datos, dict):
        raise LLMError("La respuesta del modelo no es un objeto JSON")
    return datos


def describir_esquema(esquema: dict[str, Any], omitir_nulos: bool = True) -> str:
    props: dict[str, Any] = esquema.get("properties", {})
    lineas = []
    for clave, spec in props.items():
        tipo = spec.get("type", "string")
        tipo_txt = " o ".join(tipo) if isinstance(tipo, list) else str(tipo)
        if "enum" in spec:
            tipo_txt += " (uno de: " + ", ".join(map(str, spec["enum"])) + ")"
        lineas.append(f'- "{clave}": {tipo_txt}')
    cabecera = "Responde únicamente con un objeto JSON. Claves posibles:\n"
    pie = (
        "\nOmite las claves cuyo valor sería null: incluye solo lo que el texto diga."
        if omitir_nulos
        else "\nIncluye todas las claves."
    )
    return cabecera + "\n".join(lineas) + pie


def _validar(datos: dict[str, Any], esquema: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = esquema.get("properties", {})
    salida: dict[str, Any] = {}
    for clave, spec in props.items():
        valor = datos.get(clave)
        if isinstance(valor, str) and valor.strip().lower() in {"", "null", "none", "n/a"}:
            valor = None
        if "enum" in spec and valor not in spec["enum"]:
            valor = None
        salida[clave] = valor
    return salida


class ModeloOllama:
    def __init__(
        self,
        url: str,
        modelo: str,
        timeout_s: float = 120.0,
        num_predict: int = 160,
        num_ctx: int = 2048,
        keep_alive: str = "30m",
    ) -> None:
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.nombre = f"ollama/{modelo}"
        self.num_predict = num_predict
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self._cliente = httpx.AsyncClient(timeout=timeout_s)

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:
        """`instruccion_formato=None` describe el esquema automáticamente; "" indica que el
        prompt de sistema ya incluye el formato."""
        if instruccion_formato is None:
            instruccion_formato = describir_esquema(esquema)
        contenido = f"{sistema}\n\n{instruccion_formato}" if instruccion_formato else sistema
        cuerpo = {
            "model": self.modelo,
            "messages": [
                {"role": "system", "content": contenido},
                {"role": "user", "content": usuario},
            ],
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }
        try:
            r = await self._cliente.post(f"{self.url}/api/chat", json=cuerpo)
            r.raise_for_status()
            contenido = r.json()["message"]["content"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"Error llamando a Ollama: {e}") from e
        return _validar(_extraer_json(contenido), esquema)

    async def disponible(self) -> bool:
        try:
            r = await self._cliente.get(f"{self.url}/api/tags")
            modelos = r.json().get("models", [])
            return r.status_code == 200 and any(m["name"] == self.modelo for m in modelos)
        except (httpx.HTTPError, ValueError):
            return False

    async def calentar(self) -> None:
        """Carga el modelo en GPU para que el primer turno real no pague la carga."""
        try:
            await self.extraer(
                "Devuelve JSON.",
                "hola",
                {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )
        except LLMError:
            pass
