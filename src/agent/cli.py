"""Punto de entrada por terminal: uv run agent "tu pregunta" """

import argparse
import os

from dotenv import load_dotenv

from agent.faults import FaultInjector, parse_fault
from agent.loop import run_agent


def main() -> None:
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
    args = parser.parse_args()

    spec = args.fault or os.environ.get("AGENT_FAULT")
    injector = None
    if spec:
        injector = FaultInjector(parse_fault(spec))
        print(f"[fault injection ACTIVA] {injector.config}")

    load_dotenv()  # carga OPENAI_API_KEY del .env
    run_agent(args.question, injector=injector)


if __name__ == "__main__":
    main()
