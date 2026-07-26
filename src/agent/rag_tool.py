"""Tool de busqueda respaldada por el RAG (Proyecto 1).

Mismo nombre y mismo schema que la tool de tabla (search_financials):
el modelo ve un contrato identico y solo cambia el fondo, que es la
variable a comparar. La tabla NO se borra; el backend se elige por flag.

Desde la fase 3, la busqueda usa un WORKER PERSISTENTE: el wrapper del RAG
arranca una vez por corrida (indice cargado una sola vez, ~10s) y atiende
todas las busquedas por stdin/stdout (~1-3s cada una), en vez de arrancar
de cero por llamada (~10-40s). El banner del reranker se hereda por stderr
y sigue gritando en la consola; las etiquetas [rag|reranker=...] en cada
resultado no cambian.

Modo de falla nuevo respecto a la tabla: el RAG puede traer fragmentos
que no contienen el dato pedido. Dos guardas lo convierten en error
legible para el modelo (nunca chunks inutiles en silencio):
1. Ningun fragmento coincide con la empresa/anio pedidos.
2. Los fragmentos coinciden pero no traen ni un digito.
Y el worker suma el suyo: si muere o se cuelga, error legible y se
relanza en la siguiente llamada.
"""

import json
import re
import select
import subprocess

from agent.paths import PROJECT_ROOT

RAG_ROOT = PROJECT_ROOT.parent / "RAG - Portfolio"
RAG_PYTHON = RAG_ROOT / ".venv" / "bin" / "python"
WRAPPER = PROJECT_ROOT / "scripts" / "rag_search.py"

STARTUP_TIMEOUT_S = 240  # arranque: proceso + indice + cliente
QUERY_TIMEOUT_S = 180  # por consulta (analyzer + embedding + rerank)
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
    return (
        f"{chunk['company']} 10-K FY{chunk['fiscal_year']}, chunk {chunk['chunk_no']}"
    )


def format_result(payload: dict, company: str, year: int, max_chars) -> str:
    """Guardas de relevancia + formato del resultado. Funcion pura."""
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


class RagWorker:
    """Proceso persistente del wrapper (--serve).

    Arranca y carga el indice UNA vez; atiende busquedas por lineas JSON.
    stderr se hereda: el banner del reranker sigue visible en la consola.
    Cuando el agente termina, el pipe de stdin se cierra y el worker muere
    solo (no queda proceso zombi).
    """

    def __init__(self, no_rerank: bool):
        cmd = [str(RAG_PYTHON), str(WRAPPER), "--rag-root", str(RAG_ROOT), "--serve"]
        if no_rerank:
            cmd.append("--no-rerank")
        self._proc = subprocess.Popen(  # noqa: S603 - argumentos fijos, sin shell
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        ready = self._read_line(STARTUP_TIMEOUT_S)
        if not ready or not json.loads(ready).get("ready"):
            self.close()
            raise RuntimeError(
                f"el worker del RAG no arranco en {STARTUP_TIMEOUT_S}s"
            )

    def _read_line(self, timeout: float):
        readable, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not readable:
            return None  # timeout
        return self._proc.stdout.readline()

    def alive(self) -> bool:
        return self._proc.poll() is None

    def search(self, query: str, top: int) -> dict:
        request = json.dumps({"query": query, "top": top}, ensure_ascii=False)
        self._proc.stdin.write(request + "\n")
        self._proc.stdin.flush()
        line = self._read_line(QUERY_TIMEOUT_S)
        if line is None:
            self.close()
            raise TimeoutError(f"la busqueda RAG excedio {QUERY_TIMEOUT_S}s")
        if not line:
            raise RuntimeError("el worker del RAG murio a media consulta")
        return json.loads(line)

    def close(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass


# Las DOS configuraciones oficiales, lado a lado. La divergencia es
# INTENCIONAL y este es su unico lugar:
# - "cli" (demo interactiva): reranker ON (mejor retrieval), recorte a
#   1,500 chars y top-3 (respuestas agiles, contexto corto).
# - "eval" (corridas de evaluacion): reranker OFF (la trial key de Cohere
#   a ~10 llamadas/min no aguanta un eval), chunk COMPLETO (el recorte
#   amputaba tablas e inducia alucinaciones; known-issues #8) y top-2
#   (fase 3: recorta ~1/3 de tokens por busqueda; riesgo declarado de
#   recall si el dato vivia en el chunk 3 — la re-medicion lo dira).
RAG_PROFILES = {
    "cli": {"no_rerank": False, "max_chars": MAX_CHARS_PER_CHUNK, "top_k": 3},
    "eval": {"no_rerank": True, "max_chars": None, "top_k": 2},
}


def rag_tool_functions(profile: str) -> dict:
    """Tools del agente con search_financials respaldada por el RAG.

    `profile` es una de las llaves de RAG_PROFILES. Mismo contrato de tools
    que la tabla; solo cambia el fondo de la busqueda.
    """
    from agent.tools import TOOL_FUNCTIONS

    config = RAG_PROFILES[profile]
    functions = dict(TOOL_FUNCTIONS)
    functions["search_financials"] = make_rag_search(**config)
    return functions


def make_rag_search(
    no_rerank: bool = False, max_chars=MAX_CHARS_PER_CHUNK, top_k: int = 3
):
    """Fabrica la tool con reranker, recorte y top_k fijados desde afuera.

    El modelo solo manda (company, year, metric); la configuracion la
    decide quien arma el agente (CLI o harness) y queda gritada en el
    banner, en el trace y en la metadata de evals. El worker persistente
    se crea perezosamente en la primera busqueda y se reusa; si muere, la
    llamada devuelve error legible y la siguiente lo relanza.
    """
    holder = [None]  # el worker vive aqui, uno por tool fabricada

    def search_financials(company: str, year: int, metric: str) -> str:
        try:
            year = int(year)
        except (TypeError, ValueError):
            return f"Error: ano invalido {year!r}"

        try:
            if holder[0] is None or not holder[0].alive():
                holder[0] = RagWorker(no_rerank)
            payload = holder[0].search(build_query(company, year, metric), top_k)
        except Exception as exc:
            if holder[0] is not None:
                holder[0].close()
                holder[0] = None
            return f"Error: la busqueda RAG fallo: {type(exc).__name__}: {exc}"

        if "error" in payload:
            return f"Error: la busqueda RAG fallo: {payload['error']}"
        return format_result(payload, company, year, max_chars)

    return search_financials
