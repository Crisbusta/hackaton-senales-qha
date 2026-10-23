import logging

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configura el logging raíz con un formato consistente.

    Regla del proyecto: nunca registrar secretos ni contenido financiero completo
    de los mensajes. Registrar identificadores (MessageSid, user_id enmascarado).
    """
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)
    # Uvicorn configura sus propios loggers; los alineamos al mismo nivel.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(level.upper())


def mask_phone(phone: str) -> str:
    """Enmascara un número para logs: +56912345678 -> ***5678."""
    return f"***{phone[-4:]}" if len(phone) > 4 else "***"
