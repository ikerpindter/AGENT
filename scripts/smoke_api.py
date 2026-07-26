"""Prueba de humo de la API: una sola llamada barata a gpt-5.4-nano.

Confirma que la llave del .env funciona y que la cuenta tiene acceso al
modelo. No es parte del agente; es un script desechable de verificacion.
Uso: uv run python scripts/smoke_api.py
"""

from dotenv import load_dotenv
from openai import OpenAI

MODEL = "gpt-5.4-nano"


def main() -> None:
    load_dotenv()  # carga OPENAI_API_KEY del .env al entorno
    client = OpenAI()  # el SDK lee OPENAI_API_KEY del entorno solo

    try:
        response = client.responses.create(
            model=MODEL,
            input="Di hola breve",
            max_output_tokens=64,
        )
    except Exception as exc:  # noqa: BLE001 - humo: reporta cualquier falla
        print("FALLO la llamada a la API.")
        print(f"Tipo de error: {type(exc).__name__}")
        print(f"Detalle completo:\n{exc}")
        raise SystemExit(1)

    print(f"Status: {response.status}")
    print(f"Modelo que respondio: {response.model}")
    print(f"Respuesta: {response.output_text!r}")
    if response.status != "completed":
        print(f"Detalle de incompleto: {response.incomplete_details}")

    usage = response.usage
    print(f"Tokens de entrada: {usage.input_tokens}")
    print(f"Tokens de salida: {usage.output_tokens} (incluye los de razonamiento)")
    print(f"Tokens totales: {usage.total_tokens}")


if __name__ == "__main__":
    main()
