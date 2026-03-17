from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-change-me")

DEBUG = True

ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "projects_ui",
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

ROOT_URLCONF = "energy_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "energy_web.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Europe/Brussels"

USE_I18N = True

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = REPO_ROOT / "staticfiles"
STATICFILES_DIRS = [REPO_ROOT / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Project-specific paths
PROJECTS_ROOT = os.environ.get(
    "DATA_PROJECTS_ROOT", str(REPO_ROOT / "data" / "projects")
)
OUTPUT_ROOT = os.environ.get("PIPELINE_OUTPUT_ROOT", str(REPO_ROOT / "output"))

