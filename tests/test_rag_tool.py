"""Tests de la tool RAG: subprocess simulado, cero red, cero RAG real."""

import json
from types import SimpleNamespace

import agent.rag_tool as rag_tool
from agent.rag_tool import MAX_CHARS_PER_CHUNK, build_query, make_rag_search


def _chunk(company="Lennar", year=2024, no=44, text="Total revenues 35,441,452"):
    return {
        "doc_id": "x",
        "company": company,
        "fiscal_year": year,
        "chunk_no": no,
        "text": text,
    }


def _fake_run(payload=None, returncode=0, stderr="", capture=None):
    def run(cmd, **kwargs):
        if capture is not None:
            capture.append(cmd)
        stdout = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    return run


def test_build_query_incluye_metrica_empresa_y_anio():
    q = build_query("D.R. Horton", 2024, "cost_of_home_sales")
    assert "costo de las casas vendidas" in q
    assert "D.R. Horton" in q
    assert "2024" in q


def test_camino_feliz_etiqueta_y_fragmentos(monkeypatch):
    payload = {"reranker": "on", "mode": "filtrado", "chunks": [_chunk()]}
    monkeypatch.setattr(rag_tool.subprocess, "run", _fake_run(payload))

    result = make_rag_search()("Lennar", 2024, "total_revenues")

    assert result.startswith("[rag|reranker=on|modo=filtrado]")
    assert "Lennar 10-K FY2024, chunk 44" in result
    assert "35,441,452" in result
    assert "MILES" in result  # la nota de unidades siempre viaja


def test_no_rerank_agrega_el_flag_y_la_etiqueta(monkeypatch):
    seen = []
    payload = {"reranker": "off", "mode": "filtrado", "chunks": [_chunk()]}
    monkeypatch.setattr(rag_tool.subprocess, "run", _fake_run(payload, capture=seen))

    result = make_rag_search(no_rerank=True)("Lennar", 2024, "total_revenues")

    assert "--no-rerank" in seen[0]
    assert result.startswith("[rag|reranker=off")


def test_guarda_1_fragmentos_de_otra_empresa_o_anio(monkeypatch):
    payload = {
        "reranker": "on",
        "mode": "sin filtro",
        "chunks": [_chunk(company="Lennar", year=2023, no=36)],
    }
    monkeypatch.setattr(rag_tool.subprocess, "run", _fake_run(payload))

    result = make_rag_search()("D.R. Horton", 2024, "total_revenues")

    assert result.startswith("Error:")
    assert "D.R. Horton FY2024" in result
    assert "Lennar 10-K FY2023" in result  # dice que trajo, no calla


def test_guarda_2_fragmentos_sin_cifras(monkeypatch):
    payload = {
        "reranker": "on",
        "mode": "filtrado",
        "chunks": [_chunk(text="Prosa narrativa sobre estrategia sin datos")],
    }
    monkeypatch.setattr(rag_tool.subprocess, "run", _fake_run(payload))

    result = make_rag_search()("Lennar", 2024, "total_revenues")

    assert result.startswith("Error:")
    assert "no contienen cifras" in result


def test_cero_fragmentos_es_error_legible(monkeypatch):
    payload = {"reranker": "on", "mode": "sin filtro", "chunks": []}
    monkeypatch.setattr(rag_tool.subprocess, "run", _fake_run(payload))

    result = make_rag_search()("Lennar", 2024, "total_revenues")

    assert result.startswith("Error:")


def test_wrapper_que_truena_se_vuelve_error_legible(monkeypatch):
    monkeypatch.setattr(
        rag_tool.subprocess,
        "run",
        _fake_run(returncode=1, stderr="Traceback...\nRuntimeError: analizador"),
    )

    result = make_rag_search()("Lennar", 2024, "total_revenues")

    assert result.startswith("Error: la busqueda RAG fallo:")
    assert "RuntimeError: analizador" in result


def test_fragmento_largo_se_recorta(monkeypatch):
    payload = {
        "reranker": "on",
        "mode": "filtrado",
        "chunks": [_chunk(text="9" * (MAX_CHARS_PER_CHUNK + 500))],
    }
    monkeypatch.setattr(rag_tool.subprocess, "run", _fake_run(payload))

    result = make_rag_search()("Lennar", 2024, "total_revenues")

    assert "…[recortado]" in result
    assert "9" * (MAX_CHARS_PER_CHUNK + 1) not in result
