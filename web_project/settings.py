"""Réglages Django : application sans base de données (tout est calculé à la volée)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
if not DEBUG and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("Définir DJANGO_SECRET_KEY quand DJANGO_DEBUG != 1.")
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = ["trips"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# Les tuiles OpenStreetMap exigent un en-tête Referer. Django envoie par défaut "same-origin",
# ce qui supprime le Referer vers les sites tiers et provoque l'erreur 403 sur la carte.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Fond de carte (modifiable, ex. pour un autre fournisseur de tuiles).
TILE_URL = os.environ.get("EV_TRIP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
TILE_ATTRIBUTION = os.environ.get("EV_TRIP_TILE_ATTRIBUTION", "&copy; contributeurs OpenStreetMap")

ROOT_URLCONF = "web_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

WSGI_APPLICATION = "web_project.wsgi.application"
DATABASES: dict = {}

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
