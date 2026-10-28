"""User-facing error texts: a short cause (no URL, no technical tail); the full error goes to the application log."""
from __future__ import annotations

import re

_URL = re.compile(r"\s+for url:\s*\S+")  # `requests` appends the full URL to HTTP errors
MAX_LEN = 160


def user_message(exc: BaseException) -> str:
    """Short cause of an error, e.g. "Calcul d'itinéraire impossible : 400 Client Error: Bad Request"."""
    text = " ".join(_URL.sub("", str(exc)).split()) or type(exc).__name__
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 1] + "…"
