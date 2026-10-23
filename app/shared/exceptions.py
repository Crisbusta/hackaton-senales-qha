"""Jerarquía de errores de la aplicación.

- InvalidRequestError: la entrada no cumple el formato esperado (→ 400).
- IntegrationError: falló un servicio externo (Twilio, LLM, base de datos).
- Cualquier otra excepción se trata como error interno.
"""


class ContigoError(Exception):
    """Base de los errores controlados de la aplicación."""


class InvalidRequestError(ContigoError):
    """Entrada inválida o incompleta."""


class IntegrationError(ContigoError):
    """Fallo al comunicarse con un servicio externo."""
