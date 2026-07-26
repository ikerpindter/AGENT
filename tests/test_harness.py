"""Tests del eval harness: cero API, corredor falso inyectable."""

import json

from agent.harness import (
    MATRIX,
    QUESTIONS,
    SCENARIOS,
    aggregate,
    check_correct,
    estimate_cost,
    run_matrix,
    select_cells,
)


def test_la_matriz_tiene_13_celdas_validas():
    assert len(MATRIX) == 13
    for q, s in MATRIX:
        assert q in QUESTIONS
        assert s in SCENARIOS


def test_filtrado_por_pregunta_y_escenario():
    assert len(select_cells(questions=["b"])) == 4
    assert len(select_cells(scenarios=["baseline"])) == 5
    assert select_cells(questions=["e"], scenarios=["search_crash_once"]) == [
        ("e", "search_crash_once")
    ]
    assert len(select_cells()) == 13


def test_correctitud_numerica_con_tolerancia():
    expected = QUESTIONS["b"]["expected"]  # 23.45 +/- 0.1
    assert check_correct("El margen fue de 23.45%", expected)
    assert check_correct("aproximadamente **23.5%**", expected)
    assert not check_correct("El margen fue de 20.3%", expected)
    assert not check_correct(None, expected)


def test_correctitud_numerica_con_formato_de_miles():
    expected = QUESTIONS["a"]["expected"]  # 35441.5 +/- 1
    assert check_correct("Fueron **35,441.5 millones de USD**", expected)
    assert not check_correct("Fueron 34,233.4 millones", expected)


def test_correctitud_de_eleccion_primera_mencion():
    expected = QUESTIONS["d"]["expected"]
    assert check_correct("D.R. Horton supero a Lennar en 2024", expected)
    assert check_correct("Fue D.R. Horton", expected)
    assert not check_correct("Fue Lennar, no D.R. Horton", expected)
    assert not check_correct("No pude obtener los datos", expected)


def test_fragilidad_documentada_de_primera_mencion():
    # LIMITACION CONOCIDA (no un bug silencioso): una respuesta CORRECTA
    # que menciona primero a la perdedora se marca incorrecta. Si esto
    # cambia, actualizar docstring de check_correct y auditoria manual.
    expected = QUESTIONS["e"]["expected"]
    assert not check_correct(
        "Lennar bajo ~1 punto, mientras D.R. Horton se mantuvo", expected
    )


def test_estimacion_de_costo_escala_con_n():
    cells = [("b", "baseline")]
    assert estimate_cost(cells, 10) == round(10 * 0.0010, 4)
    assert estimate_cost(select_cells(), 5) > 0


def test_estimacion_con_backend_rag_aplica_el_factor():
    cells = [("b", "baseline")]
    assert estimate_cost(cells, 10, "rag") == round(10 * 0.0010 * 7.0, 4)


def test_tool_functions_por_backend():
    from agent.harness import _tool_functions_for_backend
    from agent.tools import TOOL_FUNCTIONS

    assert _tool_functions_for_backend("table") is None  # tabla = default del loop
    rag_fns = _tool_functions_for_backend("rag")
    assert set(rag_fns) == set(TOOL_FUNCTIONS)  # mismas tools, mismo contrato
    assert rag_fns["search_financials"] is not TOOL_FUNCTIONS["search_financials"]
    assert rag_fns["calculator"] is TOOL_FUNCTIONS["calculator"]


def test_run_matrix_registra_backend_en_metadata(tmp_path):
    out = tmp_path / "res.json"

    def fake_runner(question_text, injector):
        return _fake_trace("ok")

    run_matrix(
        [("b", "baseline")], n=1, out_path=out, runner=fake_runner, backend="rag"
    )

    meta = json.loads(out.read_text(encoding="utf-8"))["meta"]
    assert meta["search_backend"] == "rag"
    assert meta["reranker"] == "off"
    assert "none" in str(meta["truncation_chars"])  # chunks completos, explicito


def _fake_trace(answer, status="RECOVERED", retries=0, faults=0):
    return {
        "final_answer": answer,
        "final_status": status,
        "retries": [{}] * retries,
        "faults_injected": [{}] * faults,
        "total_input_tokens": 100,
        "total_output_tokens": 20,
        "cost_usd": 0.0005,
    }


def test_run_matrix_guarda_parciales_y_pasa_el_injector(tmp_path):
    out = tmp_path / "res.json"
    seen = []

    def fake_runner(question_text, injector):
        seen.append((question_text, injector is not None))
        # Tras la primera corrida ya debe existir un parcial en disco.
        if len(seen) == 2:
            partial = json.loads(out.read_text(encoding="utf-8"))
            assert len(partial["runs"]) == 1
        return _fake_trace("El margen fue 23.45%")

    cells = [("b", "baseline"), ("b", "search_crash_once")]
    results = run_matrix(cells, n=1, out_path=out, runner=fake_runner)

    assert seen == [(QUESTIONS["b"]["text"], False), (QUESTIONS["b"]["text"], True)]
    final = json.loads(out.read_text(encoding="utf-8"))
    assert len(final["runs"]) == 2
    assert final["table"] is not None
    assert final["meta"]["finished"] is not None
    assert results["runs"][0]["correct"] is True


def test_aggregate_cuenta_estados_y_auditar():
    runs = [
        {
            "question": "b",
            "scenario": "search_crash_always",
            "rep": 1,
            "correct": False,
            "final_status": "FAILED_HONESTLY",
            "retries": 0,
            "faults": 3,
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.001,
            "final_answer": "no pude",
        },
        {
            "question": "b",
            "scenario": "search_crash_always",
            "rep": 2,
            "correct": False,
            "final_status": "HALLUCINATED",
            "retries": 1,
            "faults": 3,
            "input_tokens": 100,
            "output_tokens": 20,
            "cost_usd": 0.001,
            "final_answer": "el margen fue 20.3%",
        },
    ]
    rows = aggregate(runs)
    assert len(rows) == 1
    c = rows[0]
    assert c["n"] == 2
    assert c["correct"] == 0
    assert c["FAILED_HONESTLY"] == 1
    assert c["HALLUCINATED"] == 1
    assert c["retries_avg"] == 0.5
    assert c["a_auditar"] == ["b/search_crash_always#2"]
