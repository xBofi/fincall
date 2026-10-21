"""Exporta al banco de pruebas los turnos que calidad ha marcado como caso desde la consola.

Cada caso va a `gold_interpretacion.jsonl` (lo que el bot debería haber entendido del turno) o a
`gold_clasificacion.jsonl` (categoría y urgencia de la solicitud), con el mismo formato que los
casos escritos a mano y que lee `comparar_modelos.py` ("catalogo", "equipos", "oido" incluidos).

Por privacidad solo sale el texto del turno y la pregunta del bot: nunca la llamada, el
teléfono, el nombre del vecino (ya seudonimizado al marcar), el autor ni la nota del revisor.
No duplica casos ya presentes en el banco (misma frase, sin distinguir mayúsculas ni espacios).

Uso:
    # Desde la base de datos configurada (BC_REPOSITORIO=postgres en backend/.env)
    uv run python evals/exportar_casos.py --simular
    uv run python evals/exportar_casos.py
    # Desde el JSON de GET /api/operaciones/casos descargado de la consola
    uv run python evals/exportar_casos.py --entrada casos.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from building_call.casos_banco import quitar_telefonos  # noqa: E402

AQUI = Path(__file__).resolve().parent
INTERPRETACION = "gold_interpretacion.jsonl"
CLASIFICACION = "gold_clasificacion.jsonl"


def _texto(valor: Any) -> Any:
    """Segunda barrera: aunque el caso ya se guardó limpio, ningún teléfono sale al banco."""
    if isinstance(valor, str):
        return quitar_telefonos(valor)
    if isinstance(valor, list):
        return [_texto(v) for v in valor]
    return valor


def caso_a_linea(caso: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(fichero del banco, línea) de un caso marcado. Solo los campos del banco, en su orden."""
    entrada = _texto(caso["entrada"])
    esperado = {k: _texto(v) for k, v in caso["esperado"].items()}
    if caso.get("banco") == "clasificacion":
        urgencias = esperado.get("urgencia")
        return CLASIFICACION, {
            "solicitud": entrada,
            "categoria": esperado["categoria"],
            "urgencias": urgencias if isinstance(urgencias, list) else [urgencias],
        }
    linea: dict[str, Any] = {"nodo": caso["nodo"], "pregunta": _texto(caso.get("pregunta"))}
    if caso.get("oido"):
        linea["oido"] = _texto(caso["oido"])
    linea["entrada"] = entrada
    if caso.get("catalogo"):
        linea["catalogo"] = True
    if caso.get("equipos"):
        linea["equipos"] = [[str(i), _texto(str(d))] for i, d in caso["equipos"]]
    linea["esperado"] = esperado
    return INTERPRETACION, linea


def _norm(texto: Any) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip().casefold()


def clave(fichero: str, linea: dict[str, Any]) -> tuple[str, ...]:
    """Lo que hace que dos casos sean el mismo: la misma frase en la misma situación."""
    if fichero == CLASIFICACION:
        return (fichero, _norm(linea["solicitud"]))
    return (
        fichero,
        _norm(linea.get("nodo")),
        _norm(linea.get("pregunta")),
        _norm(linea.get("oido")),
        _norm(linea["entrada"]),
    )


def _leer(ruta: Path) -> list[dict[str, Any]]:
    if not ruta.exists():
        return []
    return [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip()]


def exportar(
    casos: list[dict[str, Any]], destino: Path = AQUI, *, simular: bool = False
) -> dict[str, int]:
    """Añade al banco los casos que no estén ya. Devuelve cuántos añade a cada fichero y cuántos
    omite por repetidos."""
    vistos = {
        clave(fichero, linea)
        for fichero in (INTERPRETACION, CLASIFICACION)
        for linea in _leer(destino / fichero)
    }
    nuevas: dict[str, list[dict[str, Any]]] = {INTERPRETACION: [], CLASIFICACION: []}
    omitidos = 0
    for caso in casos:
        fichero, linea = caso_a_linea(caso)
        k = clave(fichero, linea)
        if k in vistos:
            omitidos += 1
            continue
        vistos.add(k)
        nuevas[fichero].append(linea)
    if not simular:
        for fichero, lineas in nuevas.items():
            if not lineas:
                continue
            ruta = destino / fichero
            previo = ruta.read_text(encoding="utf-8") if ruta.exists() else ""
            separador = "" if not previo or previo.endswith("\n") else "\n"
            with ruta.open("a", encoding="utf-8", newline="\n") as f:
                f.write(separador)
                for linea in lineas:
                    f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    return {INTERPRETACION: len(nuevas[INTERPRETACION]), CLASIFICACION: len(nuevas[CLASIFICACION]),
            "omitidos": omitidos}  # fmt: skip


async def _casos_de_la_base() -> list[dict[str, Any]]:
    from building_call.settings import settings

    if settings.repositorio != "postgres":
        raise SystemExit(
            "Sin base de datos (BC_REPOSITORIO no es postgres): descarga los casos de "
            "GET /api/operaciones/casos y pásalos con --entrada"
        )
    from building_call.repos.postgres import RepositorioPostgres

    return await RepositorioPostgres(settings.database_url).listar_casos_banco()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--entrada", type=Path, help="JSON descargado de GET /api/operaciones/casos")
    ap.add_argument("--destino", type=Path, default=AQUI, help="Carpeta con los gold_*.jsonl")
    ap.add_argument("--simular", action="store_true", help="Cuenta lo que añadiría sin escribir")
    args = ap.parse_args(argv)
    if args.entrada:
        casos = json.loads(args.entrada.read_text(encoding="utf-8"))
    else:
        casos = asyncio.run(_casos_de_la_base())
    r = exportar(casos, args.destino, simular=args.simular)
    verbo = "Añadiría" if args.simular else "Añadidos"
    print(
        f"{verbo}: {r[INTERPRETACION]} a {INTERPRETACION}, {r[CLASIFICACION]} a {CLASIFICACION}; "
        f"{r['omitidos']} ya estaban en el banco"
    )


if __name__ == "__main__":
    main()
