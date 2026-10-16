"""Django settings: no database (everything is computed on the fly)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # project root

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
if not DEBUG and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("Définir DJANGO_SECRET_KEY quand DJANGO_DEBUG != 1.")
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = ["interfaces.django_ui"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# OSM tiles require a Referer; Django's default "same-origin" strips it for third parties and causes 403s.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Base map (configurable, e.g. another tile provider).
TILE_URL = os.environ.get("EV_TRIP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_ATTRIBUTION = os.environ.get("EV_TRIP_TILE_ATTRIBUTION", "&copy; contributeurs OpenStreetMap")

# Per-operator station tariffs (EUR/kWh), see tariffs.json.
TARIFFS_FILE = os.environ.get("EV_TRIP_TARIFFS", str(BASE_DIR / "tariffs.json"))

ROOT_URLCONF = "interfaces.django_ui.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

WSGI_APPLICATION = "interfaces.django_ui.wsgi.application"
DATABASES: dict = {}

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
