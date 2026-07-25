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


# Cifras verificadas contra los filings 10-K del proyecto RAG (2026-07-25).
# Normalizadas a MILLONES de USD: Lennar reporta en miles (se dividio entre
# 1,000); D.R. Horton ya reporta en millones.
# Procedencia (doc_id / chunk del indice del RAG):
# - Lennar: lennar-fy2024 chunk 44 (cross-check lennar-fy2023 chunk 36).
# - DHI ingresos totales: dhi-fy2024 chunk 71 (cross-check dhi-fy2023 chunk 68).
# - DHI casas: dhi-fy2024 chunks 82/83 (cross-check dhi-fy2023 chunks 79/80).
#   El costo de casas de DHI es la columna del segmento homebuilding
#   (25,952.1 / 24,201.3), que es la que reproduce el "home sales gross
#   margin 23.5%" que DHI publica; la columna consolidada neta eliminaciones
#   intercompania y daria 24.2%.
FINANCIAL_DATA = {
    ("Lennar", 2023): {
        "total_revenues": 34233.4,
        "home_sales_revenues": 32459.1,
        "cost_of_home_sales": 24900.5,
    },
    ("Lennar", 2024): {
        "total_revenues": 35441.5,
        "home_sales_revenues": 33778.1,
        "cost_of_home_sales": 26255.4,
    },
    ("D.R. Horton", 2023): {
        "total_revenues": 35460.4,
        "home_sales_revenues": 31641.0,
        "cost_of_home_sales": 24201.3,
    },
    ("D.R. Horton", 2024): {
        "total_revenues": 36801.4,
        "home_sales_revenues": 33903.6,
        "cost_of_home_sales": 25952.1,
    },
}

SEARCH_FINANCIALS_TOOL = {
    "type": "function",
    "name": "search_financials",
    "description": (
        "Busca cifras financieras de los filings 10-K de Lennar y D.R. Horton "
        "(anos fiscales 2023 y 2024). Todas las cifras estan en MILLONES de "
        "USD. Usa siempre esta tool para datos financieros; no uses cifras de "
        "tu memoria. Ojo: el ano fiscal de D.R. Horton termina el 30 de "
        "septiembre y el de Lennar el 30 de noviembre."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "enum": ["Lennar", "D.R. Horton"],
                "description": "La empresa",
            },
            "year": {
                "type": "integer",
                "enum": [2023, 2024],
                "description": "El ano fiscal",
            },
            "metric": {
                "type": "string",
                "enum": [
                    "total_revenues",
                    "home_sales_revenues",
                    "cost_of_home_sales",
                ],
                "description": (
                    "total_revenues = ingresos totales consolidados; "
                    "home_sales_revenues = ingresos por venta de casas; "
                    "cost_of_home_sales = costo de las casas vendidas"
                ),
            },
        },
        "required": ["company", "year", "metric"],
    },
}


def search_financials(company: str, year: int, metric: str) -> str:
    """Devuelve la cifra pedida en millones de USD, o un error legible."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        return f"Error: ano invalido {year!r}"

    row = FINANCIAL_DATA.get((company, year))
    if row is None:
        disponibles = ", ".join(sorted(f"{c} {y}" for c, y in FINANCIAL_DATA))
        return (
            f"Error: no hay datos para {company!r} en {year}. "
            f"Disponibles: {disponibles}"
        )
    value = row.get(metric)
    if value is None:
        return (
            f"Error: metrica desconocida {metric!r}. "
            f"Disponibles: {', '.join(sorted(row))}"
        )
    return f"{value} (millones de USD, {company}, ano fiscal {year}, {metric})"


# Registro de tools: schemas que se anuncian al modelo y funciones que las
# ejecutan. Las proximas tools se agregan aqui.
TOOL_SCHEMAS = [CALCULATOR_TOOL, SEARCH_FINANCIALS_TOOL]
TOOL_FUNCTIONS = {"calculator": calculator, "search_financials": search_financials}
