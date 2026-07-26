"""Punto de entrada por terminal: uv run agent "tu pregunta" """

import argparse
import os
import sys

from dotenv import load_dotenv

from agent.faults import FaultInjector, parse_fault
from agent.loop import run_agent
from agent.paths import ENV_FILE


def require_api_key() -> None:
    """Termina con instrucciones claras si falta la llave (estilo del RAG)."""
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        sys.exit(
            "ERROR: falta OPENAI_API_KEY.\n"
            "Copia .env.example como .env en la raiz del proyecto y pega tu clave:\n"
            "    OPENAI_API_KEY=sk-...\n"
        )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Agente con tool use")
    parser.add_argument("question", help="La pregunta para el agente")
    parser.add_argument(
        "--fault",
        default=None,
        help=(
            "Inyecta una falla a proposito (apagado por default). Formato: "
            "kind[,tool=NOMBRE][,step=N][,mode=once|always]. Kinds: "
            "tool_exception, tool_garbage, api_timeout, unknown_tool. "
            "Tambien se puede via la variable de entorno AGENT_FAULT."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=["table", "rag"],
        default="table",
        help=(
            "Fondo de search_financials: 'table' (12 numeros verificados, "
            "default) o 'rag' (retrieval del Proyecto 1, con reranker)."
        ),
    )
    args = parser.parse_args(argv)

    spec = args.fault or os.environ.get("AGENT_FAULT")
    injector = None
    if spec:
        injector = FaultInjector(parse_fault(spec))
        print(f"[fault injection ACTIVA] {injector.config}")

    tool_functions = None
    if args.backend == "rag":
        from agent.rag_tool import RAG_PROFILES, rag_tool_functions

        tool_functions = rag_tool_functions("cli")
        print(f"[backend: rag] perfil cli: {RAG_PROFILES['cli']}")

    load_dotenv(ENV_FILE)  # siempre el .env de la raiz del proyecto
    require_api_key()
    run_agent(args.question, injector=injector, tool_functions=tool_functions)


if __name__ == "__main__":
    main()
