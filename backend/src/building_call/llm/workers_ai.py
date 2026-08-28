"""Cliente Cloudflare Workers AI (API compatible con chat completions).

Misma interfaz que `ModeloOllama` para poder comparar en `evals/comparar_modelos.py`.
Modelos con razonamiento (qwen3.x): `chat_template_kwargs.enable_thinking=false` evita cientos de
tokens de "reasoning" (medido: 178 tokens/4 s -> 15 tokens/2 s).
"""

from __future__ import annotations

from typing import Any

import httpx

from building_call.llm.ollama import LLMError, _extraer_json, _validar, describir_esquema


class ModeloWorkersAI:
    def __init__(
        self,
        account_id: str,
        api_token: str,
        modelo: str,
        timeout_s: float = 30.0,
        max_tokens: int = 160,
        desactivar_razonamiento: bool = True,
    ) -> None:
        if not account_id or not api_token:
            raise ValueError(
                "Workers AI requiere BC_CLOUDFLARE_ACCOUNT_ID y BC_CLOUDFLARE_API_TOKEN"
            )
        self.modelo = modelo
        self.nombre = f"workers-ai/{modelo}"
        self.max_tokens = max_tokens
        self.desactivar_razonamiento = desactivar_razonamiento
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{modelo}"
        self._cliente = httpx.AsyncClient(
            timeout=timeout_s, headers={"Authorization": f"Bearer {api_token}"}
        )

    async def extraer(
        self,
        sistema: str,
        usuario: str,
        esquema: dict[str, Any],
        instruccion_formato: str | None = None,
    ) -> dict[str, Any]:
        if instruccion_formato is None:
            instruccion_formato = describir_esquema(esquema)
        contenido = f"{sistema}\n\n{instruccion_formato}" if instruccion_formato else sistema
        cuerpo: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": contenido},
                {"role": "user", "content": usuario},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.desactivar_razonamiento:
            cuerpo["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            r = await self._cliente.post(self._url, json=cuerpo)
            datos = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(f"Error llamando a Workers AI: {e}") from e
        if not datos.get("success"):
            raise LLMError(f"Workers AI error: {datos.get('errors')}")
        resultado = datos.get("result", {})
        if isinstance(resultado, dict) and "choices" in resultado:
            texto = resultado["choices"][0]["message"].get("content")
        elif isinstance(resultado, dict):
            texto = resultado.get("response")
        else:
            texto = resultado
        if not isinstance(texto, str) or not texto.strip():
            raise LLMError(
                "Workers AI no devolvió contenido (¿se agotó max_tokens en razonamiento?)"
            )
        return _validar(_extraer_json(texto), esquema)

    async def disponible(self) -> bool:
        return True

    async def calentar(self) -> None:
        return None
