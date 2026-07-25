"""Reintentos con espera creciente (exponential backoff) para la API.

Regla central: SOLO se reintenta lo transitorio, es decir, fallas donde el
mundo puede cambiar entre un intento y el siguiente (la red vuelve, el rate
limit expira, el servidor se recupera). Reintentar lo no transitorio es un
bug, no robustez: una division entre cero da cero infinitas veces, y unos
argumentos invalidos siguen invalidos al tercer intento.

Los errores de tools NUNCA pasan por aqui: se regresan al modelo como texto
legible para que el corrija (eso es recovery, no retry).
"""

import time

import openai

# Lista cerrada de lo reintentable, verificada contra el SDK instalado:
# - APIConnectionError: fallas de red; incluye APITimeoutError (hereda de el).
# - RateLimitError: HTTP 429, limite de peticiones.
# - InternalServerError: HTTP 5xx, el servidor fallo.
# Todo lo demas (BadRequestError, AuthenticationError, etc.) NO se reintenta.
RETRYABLE_EXCEPTIONS = (
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)

MAX_ATTEMPTS = 3  # tope duro: 1 intento original + 2 reintentos
BACKOFF_SECONDS = [1, 2]  # espera antes del 2do y del 3er intento


def call_with_retries(fn, on_retry=None, sleep=time.sleep):
    """Ejecuta fn(); si truena con un error transitorio, espera y reintenta.

    Cualquier error no transitorio se propaga de inmediato, sin reintento.
    Si el error transitorio persiste al agotar MAX_ATTEMPTS, se propaga.
    `on_retry(attempt, exc, wait)` permite registrar cada reintento en el
    trace. `sleep` es inyectable para que los tests no duerman de verdad.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn()
        except RETRYABLE_EXCEPTIONS as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            wait = BACKOFF_SECONDS[attempt - 1]
            if on_retry is not None:
                on_retry(attempt, exc, wait)
            sleep(wait)
