"""Django settings for marketplace-seller-collector."""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.getenv("DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_rq",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.parse(os.getenv("DATABASE_URL"), conn_max_age=60)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

RQ_QUEUES = {
    "default": {
        "HOST": os.getenv("REDIS_HOST", ""),
        "PORT": os.getenv("REDIS_PORT", ""),
        "URL": REDIS_URL,
        "DB": 0,
        # Sync mode allows running jobs without a Redis worker (dev/tests).
        "ASYNC": os.getenv("RQ_ASYNC", "1") == "1",
        "DEFAULT_TIMEOUT": 3600,
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# --- External services ---
DADATA_TOKEN = os.getenv("DADATA_TOKEN", "")
DADATA_SECRET = os.getenv("DADATA_SECRET", "")
DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
OZON_COOKIES = os.getenv("OZON_COOKIES", "")          # cookie string or JSON dict
WB_COOKIES = os.getenv("WB_COOKIES", "")
YANDEX_MARKET_COOKIES = os.getenv("YANDEX_MARKET_COOKIES", "")

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
HTTP_RATE_DELAY = float(os.getenv("HTTP_RATE_DELAY", "1.0"))
COLLECT_MAX_SELLERS = int(os.getenv("COLLECT_MAX_SELLERS", "0"))
COLLECT_CONCURRENCY = int(os.getenv("COLLECT_CONCURRENCY", "3"))
COLLECT_DETAIL_CHUNK = int(os.getenv("COLLECT_DETAIL_CHUNK", "100"))
COLLECT_PROGRESS_BATCH = int(os.getenv("COLLECT_PROGRESS_BATCH", "25"))

# DaData enrichment TTL (days): skip re-enrichment for fresh records.
DADATA_TTL_DAYS = int(os.getenv("DADATA_TTL_DAYS", "30"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "loggers": {
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
}
