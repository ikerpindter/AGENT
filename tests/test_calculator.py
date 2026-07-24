"""Tests de la calculadora: puro Python, cero red."""

from agent.tools import calculator


def test_suma():
    assert calculator("add", 15, 27) == "42"


def test_resta():
    assert calculator("subtract", 10, 4) == "6"


def test_multiplicacion():
    assert calculator("multiply", 847, 293) == "248171"


def test_division():
    assert calculator("divide", 10, 4) == "2.5"


def test_division_exacta_sin_punto_decimal():
    assert calculator("divide", 10, 2) == "5"


def test_division_entre_cero_no_revienta():
    assert calculator("divide", 5, 0) == "Error: division entre cero"


def test_operacion_desconocida_regresa_error_legible():
    assert calculator("power", 2, 3).startswith("Error:")
