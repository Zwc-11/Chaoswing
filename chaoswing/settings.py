from __future__ import annotations

"""Django settings for ChaosWing.

The public repository keeps all deployment-sensitive values in environment
variables, loaded through `chaoswing.config`. This keeps secrets out of the
codebase and makes the runtime surface explicit for contributors.
"""

from importlib.util import find_spec
from pathlib import Path
import sys

from django.core.exceptions import ImproperlyConfigured

from .config import build_runtime_config


BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME = build_runtime_config(BASE_DIR)
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"
WHITE_NOISE_AVAILABLE = find_spec("whitenoise") is not None

SECRET_KEY = RUNTIME.secret_key
DEBUG = RUNTIME.debug
ALLOWED_HOSTS = RUNTIME.allowed_hosts
CSRF_TRUSTED_ORIGINS = RUNTIME.csrf_trusted_origins

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.web",
]

MIDDLEWARE = [
    "chaoswing.middleware.RequestSizeLimitMiddleware",
    "chaoswing.middleware.RateLimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "chaoswing.middleware.SecurityHeadersMiddleware",
]

if WHITE_NOISE_AVAILABLE:
    MIDDLEWARE.insert(3, "whitenoise.middleware.WhiteNoiseMiddleware")
elif not DEBUG and not RUNNING_TESTS:
    raise ImproperlyConfigured("whitenoise must be installed when DJANGO_DEBUG=0.")

ROOT_URLCONF = "chaoswing.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

WSGI_APPLICATION = "chaoswing.wsgi.application"
ASGI_APPLICATION = "chaoswing.asgi.application"

if RUNTIME.database_url:
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(RUNTIME.database_url, conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": RUNTIME.sqlite_path,
        }
    }

AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Toronto"
USE_I18N = True
USE_TZ = True

STATIC_URL = RUNTIME.static_url
STATIC_ROOT = RUNTIME.static_root

SECURE_SSL_REDIRECT = RUNTIME.secure_ssl_redirect
SESSION_COOKIE_SECURE = RUNTIME.session_cookie_secure
CSRF_COOKIE_SECURE = RUNTIME.csrf_cookie_secure
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

if RUNTIME.trust_x_forwarded_proto:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CHAOSWING_ENABLE_REMOTE_FETCH = RUNTIME.enable_remote_fetch
CHAOSWING_ENABLE_LLM = RUNTIME.enable_llm
CHAOSWING_ANTHROPIC_API_KEY = RUNTIME.anthropic_api_key
CHAOSWING_ANTHROPIC_MODEL = RUNTIME.anthropic_model
CHAOSWING_ANTHROPIC_INPUT_COST_PER_MTOK = RUNTIME.anthropic_input_cost_per_mtok
CHAOSWING_ANTHROPIC_OUTPUT_COST_PER_MTOK = RUNTIME.anthropic_output_cost_per_mtok
CHAOSWING_HTTP_TIMEOUT_SECONDS = RUNTIME.http_timeout_seconds
CHAOSWING_RATE_LIMIT_ENABLED = RUNTIME.rate_limit_enabled
CHAOSWING_MAX_REQUEST_BODY_BYTES = RUNTIME.max_request_body_bytes
CHAOSWING_TRENDING_CACHE_TTL = RUNTIME.trending_cache_ttl_seconds
CHAOSWING_BENCHMARK_CACHE_TTL = RUNTIME.benchmark_cache_ttl_seconds
CHAOSWING_LEADLAG_CACHE_TTL = RUNTIME.leadlag_cache_ttl_seconds
CHAOSWING_LEADLAG_POLL_SECONDS = RUNTIME.leadlag_default_poll_seconds
CHAOSWING_LEADLAG_TRADE_HORIZON_SECONDS = RUNTIME.leadlag_default_trade_horizon_seconds
CHAOSWING_MLFLOW_TRACKING_URI = RUNTIME.mlflow_tracking_uri
CHAOSWING_MLFLOW_EXPERIMENT = RUNTIME.mlflow_experiment
CHAOSWING_KALSHI_API_BASE = RUNTIME.kalshi_api_base
CHAOSWING_KALSHI_WS_URL = RUNTIME.kalshi_ws_url
CHAOSWING_KALSHI_DEMO_API_BASE = RUNTIME.kalshi_demo_api_base
CHAOSWING_KALSHI_ACCESS_KEY_ID = RUNTIME.kalshi_access_key_id
CHAOSWING_KALSHI_PRIVATE_KEY_PATH = RUNTIME.kalshi_private_key_path
CHAOSWING_POLYMARKET_WS_URL = RUNTIME.polymarket_ws_url

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": RUNTIME.log_level,
    },
    "loggers": {
        "apps.web": {
            "handlers": ["console"],
            "level": RUNTIME.log_level,
            "propagate": False,
        },
        "chaoswing": {
            "handlers": ["console"],
            "level": RUNTIME.log_level,
            "propagate": False,
        },
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Manifest-backed static storage requires collectstatic output, so keep tests
# and local debug renders on the plain backend.
staticfiles_backend = "django.contrib.staticfiles.storage.StaticFilesStorage"
if not DEBUG and not RUNNING_TESTS:
    if not WHITE_NOISE_AVAILABLE:
        raise ImproperlyConfigured("whitenoise must be installed when DJANGO_DEBUG=0.")
    staticfiles_backend = "whitenoise.storage.CompressedManifestStaticFilesStorage"

STORAGES = {
    "staticfiles": {
        "BACKEND": staticfiles_backend,
    },
}

if not DEBUG:
    SECURE_HSTS_SECONDS = 63072000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
