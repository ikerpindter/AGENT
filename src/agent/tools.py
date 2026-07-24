"""Tools del agente. Rebanada 1: solo la calculadora.

Sin eval(): el tool schema obliga al modelo a mandar la operacion como un
enum (lista cerrada de opciones) y los dos numeros ya separados. Aqui no se
parsea texto de matematicas; solo hay aritmetica explicita.
"""

CALCULATOR_TOOL = {
    "type": "function",
    "name": "calculator",
    "description": (
        "Realiza una operacion aritmetica entre dos numeros. "
        "Usala para cualquier calculo en vez de calcular por tu cuenta."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["add", "subtract", "multiply", "divide"],
                "description": "La operacion a realizar",
            },
            "a": {"type": "number", "description": "Primer operando"},
            "b": {"type": "number", "description": "Segundo operando"},
        },
        "required": ["operation", "a", "b"],
    },
}


def calculator(operation: str, a: float, b: float) -> str:
    """Ejecuta la operacion pedida y regresa el resultado como texto.

    Los errores se regresan como texto legible, no como excepcion, para que
    el modelo pueda leerlos y reaccionar.
    """
    if operation == "add":
        result = a + b
    elif operation == "subtract":
        result = a - b
    elif operation == "multiply":
        result = a * b
    elif operation == "divide":
        if b == 0:
            return "Error: division entre cero"
        result = a / b
    else:
        return f"Error: operacion desconocida {operation!r}"

    # Enteros sin ".0" para que el modelo no arrastre decimales de ruido.
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


# Registro de tools: schemas que se anuncian al modelo y funciones que las
# ejecutan. Las proximas tools se agregan aqui.
TOOL_SCHEMAS = [CALCULATOR_TOOL]
TOOL_FUNCTIONS = {"calculator": calculator}
