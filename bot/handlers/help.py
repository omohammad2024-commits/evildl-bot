"""/help lives in start.py; this module keeps the import path stable."""
from bot.handlers.start import help_command  # noqa: F401

__all__ = ["help_command"]
