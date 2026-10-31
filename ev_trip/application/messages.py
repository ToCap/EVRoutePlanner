"""User-facing error texts: a short cause (no URL, no technical tail); the full error goes to the application log."""
from __future__ import annotations

import re

_URL = re.compile(r"\s+for url:\s*\S+")  # `requests` appends the full URL to HTTP errors
_POOL = re.compile(r"\s*:?\s*\w*ConnectionPool\(.*$")  # `requests` connection failures: host, port, retries, URL
MAX_LEN = 160


def user_message(exc: BaseException) -> str:
    """Short cause of an error, e.g. "Calcul d'itinéraire impossible : 400 Client Error: Bad Request"."""
    text = " ".join(_URL.sub("", str(exc)).split()) or type(exc).__name__
    pool = _POOL.search(text)
    if pool:  # keep what failed, replace the technical tail by a short cause
        text = f"{text[: pool.start()]} ({'délai dépassé' if 'timed out' in pool.group(0) else 'connexion au serveur impossible'})".strip()
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 1] + "…"
