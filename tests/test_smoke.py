def test_package_importa():
    """El paquete agent se importa sin errores."""
    import agent

    assert agent is not None
