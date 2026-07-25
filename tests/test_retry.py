"""Tests del backoff: cero red, errores fabricados y espera falsa."""

import httpx
import openai
import pytest

from agent.retry import MAX_ATTEMPTS, call_with_retries


def _timeout_error():
    return openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )


class Flaky:
    """Funcion que falla n veces y luego regresa un valor."""

    def __init__(self, failures, exc_factory):
        self.failures = failures
        self.exc_factory = exc_factory
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc_factory()
        return "ok"


def test_reintenta_lo_transitorio_y_recupera():
    fn = Flaky(failures=2, exc_factory=_timeout_error)
    waits = []
    retries = []

    result = call_with_retries(
        fn,
        on_retry=lambda attempt, exc, wait: retries.append((attempt, wait)),
        sleep=waits.append,
    )

    assert result == "ok"
    assert fn.calls == 3
    assert waits == [1, 2]  # espera creciente
    assert retries == [(1, 1), (2, 2)]


def test_no_reintenta_lo_no_reintentable():
    fn = Flaky(failures=1, exc_factory=lambda: ValueError("argumento invalido"))
    waits = []

    with pytest.raises(ValueError):
        call_with_retries(fn, sleep=waits.append)

    assert fn.calls == 1  # ni un reintento: reintentar esto seria un bug
    assert waits == []


def test_respeta_el_tope_de_intentos():
    fn = Flaky(failures=99, exc_factory=_timeout_error)
    waits = []

    with pytest.raises(openai.APITimeoutError):
        call_with_retries(fn, sleep=waits.append)

    assert fn.calls == MAX_ATTEMPTS  # exactamente 3, ni uno mas
    assert waits == [1, 2]
