"""Tool de busqueda respaldada por el RAG (Proyecto 1).

Mismo nombre y mismo schema que la tool de tabla (search_financials):
el modelo ve un contrato identico y solo cambia el fondo, que es la
variable a comparar. La tabla NO se borra; el backend se elige por flag.

Modo de falla nuevo respecto a la tabla: el RAG puede traer fragmentos
que no contienen el dato pedido. Dos guardas lo convierten en error
legible para el modelo (nunca chunks inutiles en silencio):
1. Ningun fragmento coincide con la empresa/anio pedidos.
2. Los fragmentos coinciden pero no traen ni un digito.
"""

import json
import re
import subprocess

from agent.paths import PROJECT_ROOT

RAG_ROOT = PROJECT_ROOT.parent / "RAG - Portfolio"
RAG_PYTHON = RAG_ROOT / ".venv" / "bin" / "python"
WRAPPER = PROJECT_ROOT / "scripts" / "rag_search.py"

SUBPROCESS_TIMEOUT_S = 240
MAX_CHARS_PER_CHUNK = 1500

_METRIC_PHRASES = {
    "total_revenues": "ingresos totales (total revenues)",
    "home_sales_revenues": "ingresos por venta de casas (home sales revenues)",
    "cost_of_home_sales": "costo de las casas vendidas (cost of home sales)",
}


def build_query(company: str, year: int, metric: str) -> str:
    phrase = _METRIC_PHRASES.get(metric, metric)
    return f"{phrase} de {company} en el año fiscal {year}"


def _label(chunk: dict) -> str:
    return f"{chunk['company']} 10-K FY{chunk['fiscal_year']}, chunk {chunk['chunk_no']}"


def _search_rag(company: str, year, metric: str, no_rerank: bool, max_chars) -> str:
    try:
        year = int(year)
    except (TypeError, ValueError):
        return f"Error: ano invalido {year!r}"

    cmd = [
        str(RAG_PYTHON),
        str(WRAPPER),
        "--rag-root",
        str(RAG_ROOT),
        "--query",
        build_query(company, year, metric),
        "--top",
        "3",
    ]
    if no_rerank:
        cmd.append("--no-rerank")

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return (
            f"Error: la busqueda RAG excedio {SUBPROCESS_TIMEOUT_S}s y se corto. "
            "Puedes reintentar."
        )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return "Error: la busqueda RAG fallo: " + " | ".join(tail)

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "Error: la busqueda RAG devolvio una salida ilegible (no JSON)."

    chunks = payload.get("chunks", [])
    if not chunks:
        return "Error: la busqueda RAG no devolvio ningun fragmento."

    # Guarda 1: los fragmentos deben ser de la empresa y anio pedidos.
    matching = [
        c for c in chunks if c["company"] == company and c["fiscal_year"] == year
    ]
    if not matching:
        received = ", ".join(_label(c) for c in chunks)
        return (
            f"Error: la busqueda no trajo fragmentos de {company} FY{year}; "
            f"trajo: [{received}]. El dato pedido puede no estar en el corpus."
        )

    # Guarda 2: fragmentos correctos pero sin una sola cifra no sirven para
    # una pregunta financiera.
    if not any(re.search(r"\d", c["text"]) for c in matching):
        return (
            f"Error: los fragmentos de {company} FY{year} recuperados no "
            "contienen cifras. Reintenta con otra metrica o formula distinta."
        )

    header = f"[rag|reranker={payload['reranker']}|modo={payload['mode']}]"
    parts = [header]
    for c in matching:
        text = c["text"]
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + "…[recortado]"
        parts.append(f"--- [{_label(c)}] ---\n{text}")
    parts.append(
        "NOTA: cifras textuales del filing, en las unidades originales del "
        "documento (Lennar reporta en MILES de USD; D.R. Horton en MILLONES)."
    )
    return "\n\n".join(parts)


def make_rag_search(no_rerank: bool = False, max_chars=MAX_CHARS_PER_CHUNK):
    """Fabrica la tool con reranker y recorte fijados desde afuera.

    El modelo solo manda (company, year, metric); reranker y recorte los
    decide quien arma el agente (CLI o harness) y quedan gritados en el
    banner, en el trace y en la metadata de evals.

    max_chars=None manda el chunk COMPLETO. Justificacion (medida
    2026-07-25): el chunker del RAG topa en ~4,000 chars y los renglones
    criticos viven hasta la posicion 3,966; cualquier recorte menor amputa
    filas de tablas y invita al modelo a completarlas inventando (visto en
    el eval rag con recorte de 1,500: 5/5 totales fabricados en la
    pregunta a).
    """

    def search_financials(company: str, year: int, metric: str) -> str:
        return _search_rag(company, year, metric, no_rerank, max_chars)

    return search_financials
