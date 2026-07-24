"""Punto de entrada por terminal: uv run agent "tu pregunta" """

import argparse

from dotenv import load_dotenv

from agent.loop import run_agent


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agente con tool use (rebanada 1: calculadora)"
    )
    parser.add_argument("question", help="La pregunta para el agente")
    args = parser.parse_args()

    load_dotenv()  # carga OPENAI_API_KEY del .env
    run_agent(args.question)


if __name__ == "__main__":
    main()
