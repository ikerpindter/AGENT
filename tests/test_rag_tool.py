"""Tests de la tool RAG: worker simulado, cero red, cero RAG real."""

import json

from agent import rag_tool
from agent.rag_tool import (
    MAX_CHARS_PER_CHUNK,
    RAG_PROFILES,
    build_query,
    format_result,
    make_rag_search,
)


def _chunk(company="Lennar", year=2024, no=44, text="Total revenues 35,441,452"):
    return {
        "doc_id": "x",
        "company": company,
        "fiscal_year": year,
        "chunk_no": no,
        "text": text,
    }


def _payload(chunks, reranker="on", mode="filtrado"):
    return {"reranker": reranker, "mode": mode, "chunks": chunks}


# ---------- perfiles y query ----------


def test_los_dos_perfiles_son_los_documentados():
    # La divergencia cli/eval es intencional; este test la congela para que
    # cualquier cambio sea consciente, no accidental.
    assert RAG_PROFILES["cli"] == {"no_rerank": False, "max_chars": None, "top_k": 3}
    assert RAG_PROFILES["eval"] == {"no_rerank": True, "max_chars": None, "top_k": 3}


def test_build_query_incluye_metrica_empresa_y_anio():
    q = build_query("D.R. Horton", 2024, "cost_of_home_sales")
    assert "costo de las casas vendidas" in q
    assert "D.R. Horton" in q
    assert "2024" in q


# ---------- guardas y formato (funcion pura) ----------


def test_camino_feliz_etiqueta_y_fragmentos():
    result = format_result(_payload([_chunk()]), "Lennar", 2024, MAX_CHARS_PER_CHUNK)
    assert result.startswith("[rag|reranker=on|modo=filtrado]")
    assert "Lennar 10-K FY2024, chunk 44" in result
    assert "35,441,452" in result
    assert "MILES" in result  # la nota de unidades siempre viaja


def test_guarda_1_fragmentos_de_otra_empresa_o_anio():
    payload = _payload([_chunk(company="Lennar", year=2023, no=36)], mode="sin filtro")
    result = format_result(payload, "D.R. Horton", 2024, None)
    assert result.startswith("Error:")
    assert "D.R. Horton FY2024" in result
    assert "Lennar 10-K FY2023" in result  # dice que trajo, no calla


def test_guarda_2_fragmentos_sin_cifras():
    payload = _payload([_chunk(text="Prosa narrativa sobre estrategia sin datos")])
    result = format_result(payload, "Lennar", 2024, None)
    assert result.startswith("Error:")
    assert "no contienen cifras" in result


def test_cero_fragmentos_es_error_legible():
    assert format_result(_payload([]), "Lennar", 2024, None).startswith("Error:")


def test_fragmento_largo_se_recorta():
    payload = _payload([_chunk(text="9" * (MAX_CHARS_PER_CHUNK + 500))])
    result = format_result(payload, "Lennar", 2024, MAX_CHARS_PER_CHUNK)
    assert "…[recortado]" in result
    assert "9" * (MAX_CHARS_PER_CHUNK + 1) not in result


def test_max_chars_none_manda_el_chunk_completo():
    texto = "9" * (MAX_CHARS_PER_CHUNK + 500)
    result = format_result(_payload([_chunk(text=texto)]), "Lennar", 2024, None)
    assert texto in result
    assert "…[recortado]" not in result


# ---------- worker persistente (proceso simulado) ----------


class FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)

    def readline(self):
        return self.lines.pop(0) if self.lines else ""


class FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass


class FakeProc:
    """Se hace pasar por el subprocess del wrapper en modo --serve."""

    def __init__(self, stdout_lines, alive=True):
        self.stdout = FakeStdout(stdout_lines)
        self.stdin = FakeStdin()
        self._alive = alive
        self.terminated = False

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True
        self._alive = False


def _fake_env(monkeypatch, procs, spawned):
    """Popen falso (entrega procs en orden) y select siempre-listo."""

    def fake_popen(cmd, **kwargs):
        spawned.append(cmd)
        return procs.pop(0)

    monkeypatch.setattr(rag_tool.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        rag_tool.select, "select", lambda r, w, x, t: (r, [], [])
    )


def _ready():
    return json.dumps({"ready": True}) + "\n"


def _result_line(chunks=None, reranker="off"):
    return json.dumps(_payload(chunks or [_chunk()], reranker=reranker)) + "\n"


def test_worker_arranca_una_vez_para_varias_busquedas(monkeypatch):
    spawned = []
    proc = FakeProc([_ready(), _result_line(), _result_line()])
    _fake_env(monkeypatch, [proc], spawned)

    search = make_rag_search(no_rerank=True, max_chars=None, top_k=2)
    r1 = search("Lennar", 2024, "total_revenues")
    r2 = search("Lennar", 2023, "total_revenues")

    assert len(spawned) == 1  # UN solo proceso para las dos busquedas
    assert "--serve" in spawned[0]
    assert "--no-rerank" in spawned[0]
    assert r1.startswith(("[rag|reranker=off", "Error:"))
    # Cada request viajo como linea JSON con el top del perfil.
    reqs = [json.loads(w) for w in proc.stdin.written]
    assert [r["top"] for r in reqs] == [2, 2]
    assert "Lennar" in reqs[0]["query"]
    # La guarda 1 sigue viva a traves del worker: la 2a busqueda pidio 2023
    # pero el fake devolvio un chunk de 2024.
    assert r2.startswith("Error:")


def test_error_del_worker_viaja_legible(monkeypatch):
    spawned = []
    error_line = json.dumps({"error": "RuntimeError: analizador fallo"}) + "\n"
    proc = FakeProc([_ready(), error_line])
    _fake_env(monkeypatch, [proc], spawned)

    result = make_rag_search(no_rerank=True)("Lennar", 2024, "total_revenues")

    assert result.startswith("Error: la busqueda RAG fallo:")
    assert "analizador" in result


def test_worker_muerto_da_error_y_se_relanza(monkeypatch):
    spawned = []
    muerto = FakeProc([_ready(), ""])  # readline vacio = proceso murio
    vivo = FakeProc([_ready(), _result_line()])
    _fake_env(monkeypatch, [muerto, vivo], spawned)

    search = make_rag_search(no_rerank=True, max_chars=None, top_k=2)
    r1 = search("Lennar", 2024, "total_revenues")
    r2 = search("Lennar", 2024, "total_revenues")

    assert r1.startswith("Error: la busqueda RAG fallo:")
    assert len(spawned) == 2  # la segunda llamada relanzo el worker
    assert not r2.startswith("Error:")


def test_arranque_colgado_da_error_legible(monkeypatch):
    spawned = []
    proc = FakeProc([_ready()])
    _fake_env(monkeypatch, [proc], spawned)
    # select sin nada listo = timeout de arranque
    monkeypatch.setattr(rag_tool.select, "select", lambda r, w, x, t: ([], [], []))

    result = make_rag_search(no_rerank=True)("Lennar", 2024, "total_revenues")

    assert result.startswith("Error: la busqueda RAG fallo:")
    assert proc.terminated  # no dejamos proceso colgado atras
