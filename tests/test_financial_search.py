"""Tests de la tool de busqueda financiera: puro Python, cero red."""

from agent.tools import FINANCIAL_DATA, search_financials


def test_cifra_correcta():
    assert search_financials("Lennar", 2024, "total_revenues") == (
        "35441.5 (millones de USD, Lennar, ano fiscal 2024, total_revenues)"
    )


def test_las_12_combinaciones_existen():
    metricas = ["total_revenues", "home_sales_revenues", "cost_of_home_sales"]
    for company in ["Lennar", "D.R. Horton"]:
        for year in [2023, 2024]:
            for metric in metricas:
                resultado = search_financials(company, year, metric)
                assert not resultado.startswith("Error"), (company, year, metric)


def test_empresa_desconocida_regresa_error_legible():
    assert search_financials("PulteGroup", 2024, "total_revenues").startswith(
        "Error:"
    )


def test_ano_sin_datos_regresa_error_legible():
    assert search_financials("Lennar", 2022, "total_revenues").startswith("Error:")


def test_metrica_desconocida_regresa_error_legible():
    assert search_financials("Lennar", 2024, "net_income").startswith("Error:")


def test_ano_como_texto_no_revienta():
    # Si el modelo manda el ano como string, la tool lo normaliza.
    assert search_financials("Lennar", "2024", "total_revenues").startswith(
        "35441.5"
    )


def test_la_tabla_sigue_en_millones():
    # Guarda contra regresiones de unidades: si alguien mete las cifras de
    # Lennar en miles (35,441,452), este test truena.
    for row in FINANCIAL_DATA.values():
        for value in row.values():
            assert 1000 < value < 100000, "cifra fuera de rango de millones"
