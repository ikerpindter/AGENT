"""Eval harness: corre la matriz preguntas x escenarios, mide y tabula.

Es orquestacion pura sobre piezas que ya existen (run_agent, FaultInjector,
extractor de numeros de verify). Produce una linea base congelada en
evals/ (versionada en git, a diferencia de traces/).

IMPORTANTE sobre las etiquetas: la tabla reporta la etiqueta CRUDA del
detector de alucinaciones, que tiene 30% de falsos positivos documentado
bajo fallas (docs/known-issues.md). Las corridas HALLUCINATED se listan
para auditoria humana; sus respuestas completas quedan en el JSON.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from agent.faults import FaultInjector, parse_fault
from agent.loop import MODEL, run_agent
from agent.verify import _extract_numbers

# Las 5 preguntas con su respuesta esperada congelada (verificada contra
# los filings 10-K en la rebanada 2).
QUESTIONS = {
    "a": {
        "text": "¿Cuáles fueron los ingresos totales de Lennar en el año fiscal 2024?",
        "expected": {"kind": "number", "value": 35441.5, "tolerance": 1.0},
    },
    "b": {
        "text": "¿Cuál fue el margen bruto de venta de casas de D.R. Horton en 2024?",
        "expected": {"kind": "number", "value": 23.45, "tolerance": 0.1},
    },
    "c": {
        "text": "¿Cuánto crecieron los ingresos totales de Lennar de 2023 a 2024, en porcentaje?",
        "expected": {"kind": "number", "value": 3.53, "tolerance": 0.05},
    },
    "d": {
        "text": "En 2024, ¿quién tuvo mayores ingresos totales, Lennar o D.R. Horton?",
        "expected": {"kind": "choice", "correct": "horton", "other": "lennar"},
    },
    "e": {
        "text": "¿A quién le fue mejor con su margen bruto de venta de casas de 2023 a 2024?",
        "expected": {"kind": "choice", "correct": "horton", "other": "lennar"},
    },
}

SCENARIOS = {
    "baseline": None,
    "search_crash_once": "tool_exception,tool=search_financials,mode=once",
    "search_crash_always": "tool_exception,tool=search_financials,mode=always",
    "api_timeout_once": "api_timeout,mode=once",
    "unknown_tool_once": "unknown_tool,tool=search_financials,mode=once",
}

# Matriz recortada: 13 celdas de 25 posibles. Cortes:
# - (c) solo en baseline: gemela mecanica de (b).
# - timeout y unknown_tool: mecanica independiente de la pregunta, 1 celda.
# - crash_once en cadena corta (b) y larga (e).
# - crash_always en a/b/d/e; la (d) mide alucinacion SIN numeros, el punto
#   ciego del detector.
MATRIX = [
    ("a", "baseline"),
    ("b", "baseline"),
    ("c", "baseline"),
    ("d", "baseline"),
    ("e", "baseline"),
    ("b", "search_crash_once"),
    ("e", "search_crash_once"),
    ("a", "search_crash_always"),
    ("b", "search_crash_always"),
    ("d", "search_crash_always"),
    ("e", "search_crash_always"),
    ("b", "api_timeout_once"),
    ("a", "unknown_tool_once"),
]

# Costo estimado por corrida (USD), con margen, a partir de costos reales
# medidos en las rebanadas 2 y 3.
COST_PER_RUN = {"a": 0.0004, "b": 0.0010, "c": 0.0011, "d": 0.0005, "e": 0.0020}
SCENARIO_COST_FACTOR = {
    "baseline": 1.0,
    "search_crash_once": 1.4,
    "search_crash_always": 0.9,
    "api_timeout_once": 1.0,
    "unknown_tool_once": 1.3,
}


def check_correct(answer, expected) -> bool:
    """Compara la respuesta final contra la esperada.

    Numericas: algun numero de la respuesta cae dentro de la tolerancia.
    De eleccion: heuristica de PRIMERA MENCION (la empresa correcta aparece
    antes que la otra). Limitacion documentada: una respuesta correcta que
    mencione primero a la perdedora ("Lennar bajo, Horton se mantuvo") se
    marca incorrecta; ante correctitud baja en (d)/(e), auditar a mano
    antes de culpar al agente.
    """
    if not answer:
        return False
    if expected["kind"] == "number":
        return any(
            abs(value - expected["value"]) <= expected["tolerance"]
            for value, _, _ in _extract_numbers(answer)
        )
    text = answer.lower()
    pos_correct = text.find(expected["correct"])
    pos_other = text.find(expected["other"])
    if pos_correct == -1:
        return False
    if pos_other == -1:
        return True
    return pos_correct < pos_other


def select_cells(questions=None, scenarios=None):
    """Filtra la matriz a un subconjunto (o completa si no hay filtros)."""
    cells = MATRIX
    if questions:
        cells = [c for c in cells if c[0] in questions]
    if scenarios:
        cells = [c for c in cells if c[1] in scenarios]
    return cells


def estimate_cost(cells, n) -> float:
    return round(
        sum(COST_PER_RUN[q] * SCENARIO_COST_FACTOR[s] for q, s in cells) * n, 4
    )


def _default_runner(question_text, injector):
    return run_agent(question_text, injector=injector)


def _save(results, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_matrix(cells, n, out_path, runner=_default_runner):
    """Corre la matriz; guarda resultados parciales tras CADA corrida."""
    results = {
        "meta": {
            "model": MODEL,
            "n": n,
            "cells": [list(c) for c in cells],
            "estimated_cost_usd": estimate_cost(cells, n),
            "started": datetime.now().isoformat(timespec="seconds"),
            "finished": None,
            "label_warning": (
                "final_status es la etiqueta CRUDA del detector (30% FP "
                "documentado bajo fallas); no es verdad verificada"
            ),
        },
        "runs": [],
        "table": None,
    }
    for q, s in cells:
        for rep in range(1, n + 1):
            spec = SCENARIOS[s]
            injector = FaultInjector(parse_fault(spec)) if spec else None
            print(f"\n>>> celda ({q}, {s}) corrida {rep}/{n}")
            trace = runner(QUESTIONS[q]["text"], injector)
            record = {
                "question": q,
                "scenario": s,
                "rep": rep,
                "correct": check_correct(
                    trace.get("final_answer"), QUESTIONS[q]["expected"]
                ),
                "final_status": trace.get("final_status"),
                "retries": len(trace.get("retries") or []),
                "faults": len(trace.get("faults_injected") or []),
                "input_tokens": trace.get("total_input_tokens", 0),
                "output_tokens": trace.get("total_output_tokens", 0),
                "cost_usd": trace.get("cost_usd", 0.0),
                "final_answer": trace.get("final_answer"),
            }
            results["runs"].append(record)
            _save(results, out_path)  # guardado parcial: nada se pierde
    results["table"] = aggregate(results["runs"])
    results["meta"]["finished"] = datetime.now().isoformat(timespec="seconds")
    _save(results, out_path)
    return results


def aggregate(runs):
    """Agrega corridas por celda (pregunta, escenario) en filas de tabla."""
    cells = {}
    for r in runs:
        key = (r["question"], r["scenario"])
        c = cells.setdefault(
            key,
            {
                "question": r["question"],
                "scenario": r["scenario"],
                "n": 0,
                "correct": 0,
                "RECOVERED": 0,
                "FAILED_HONESTLY": 0,
                "HALLUCINATED": 0,
                "retries_total": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "a_auditar": [],
            },
        )
        c["n"] += 1
        c["correct"] += int(r["correct"])
        status = r["final_status"]
        if status in ("RECOVERED", "FAILED_HONESTLY", "HALLUCINATED"):
            c[status] += 1
        c["retries_total"] += r["retries"]
        c["input_tokens"] += r["input_tokens"]
        c["output_tokens"] += r["output_tokens"]
        c["cost_usd"] = round(c["cost_usd"] + r["cost_usd"], 6)
        if status == "HALLUCINATED":
            c["a_auditar"].append(f"{r['question']}/{r['scenario']}#{r['rep']}")
    rows = []
    for c in cells.values():
        c["retries_avg"] = round(c["retries_total"] / c["n"], 2)
        rows.append(c)
    return rows


def print_table(rows):
    print()
    print("=" * 100)
    print("ETIQUETAS CRUDAS DEL DETECTOR - no verdad verificada.")
    print("FP documentado: 30% bajo fallas (docs/known-issues.md).")
    print("Toda corrida en a_auditar requiere revision humana; respuestas")
    print("completas en el JSON de evals/.")
    print("=" * 100)
    header = (
        f"{'preg':<5}{'escenario':<22}{'n':>3}{'correctas':>10}"
        f"{'RECOV':>7}{'FAIL_H':>8}{'HALLUC':>8}{'reint':>7}"
        f"{'tokens':>9}{'costo$':>10}  a_auditar"
    )
    print(header)
    print("-" * len(header))
    for c in rows:
        tokens = c["input_tokens"] + c["output_tokens"]
        audit = ", ".join(c["a_auditar"]) if c["a_auditar"] else "-"
        print(
            f"{c['question']:<5}{c['scenario']:<22}{c['n']:>3}"
            f"{c['correct']:>7}/{c['n']:<2}"
            f"{c['RECOVERED']:>7}{c['FAILED_HONESTLY']:>8}{c['HALLUCINATED']:>8}"
            f"{c['retries_avg']:>7}{tokens:>9}{c['cost_usd']:>10.4f}  {audit}"
        )
    total_cost = round(sum(c["cost_usd"] for c in rows), 4)
    total_runs = sum(c["n"] for c in rows)
    print("-" * len(header))
    print(f"TOTAL: {total_runs} corridas, ${total_cost} USD")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Eval harness del agente (matriz preguntas x escenarios)"
    )
    parser.add_argument("--n", type=int, default=5, help="repeticiones por celda")
    parser.add_argument(
        "--question", action="append", choices=sorted(QUESTIONS),
        help="limita a una pregunta (repetible)",
    )
    parser.add_argument(
        "--scenario", action="append", choices=sorted(SCENARIOS),
        help="limita a un escenario (repetible)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="muestra matriz y costo estimado; cero llamadas a la API",
    )
    parser.add_argument(
        "--max-cost", type=float, default=0.15,
        help="tope duro en USD; si la estimacion lo pasa, no corre",
    )
    parser.add_argument("--output", default=None, help="ruta del JSON de resultados")
    args = parser.parse_args(argv)

    cells = select_cells(args.question, args.scenario)
    est = estimate_cost(cells, args.n)
    print(f"Matriz: {len(cells)} celdas x N={args.n} = {len(cells) * args.n} corridas")
    for q, s in cells:
        print(f"  ({q}) {s}")
    print(f"Costo estimado: ${est} USD (tope: ${args.max_cost})")

    if args.dry_run:
        print("[dry-run] No se llamo a la API.")
        return 0
    if est > args.max_cost:
        print(f"ABORTADO: la estimacion ${est} supera el tope ${args.max_cost}.")
        return 1

    from dotenv import load_dotenv

    load_dotenv()
    stamp = datetime.now().strftime("%Y-%m-%d")
    out_path = args.output or f"evals/results_{stamp}_{MODEL}_n{args.n}.json"
    results = run_matrix(cells, args.n, out_path)
    print_table(results["table"])
    print(f"\nResultados congelados en: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
