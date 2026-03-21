from __future__ import annotations

"""Centralized runtime configuration for the public ChaosWing repository.

This module keeps environment parsing in one place so secrets, deployment
switches, and backend toggles are not scattered through the codebase.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.core.exceptions import ImproperlyConfigured


DEFAULT_SECRET_KEY = "django-insecure-chaoswing-local"


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv(path: Path) -> None:
    """Load repo-local environment values without overriding real shell vars."""

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_wrapping_quotes(value.strip())
        if key:
            os.environ.setdefault(key, value)


class EnvReader:
    """Typed helpers around process environment lookups."""

    TRUE_VALUES = {"1", "true", "yes", "on"}
    FALSE_VALUES = {"0", "false", "no", "off"}

    def __init__(self, environ: dict[str, str] | None = None):
        self.environ = environ or os.environ

    def get_str(self, key: str, default: str = "") -> str:
        return str(self.environ.get(key, default)).strip()

    def get_bool(self, key: str, default: bool = False) -> bool:
        raw_value = self.environ.get(key)
        if raw_value is None:
            return default

        normalized = str(raw_value).strip().lower()
        if normalized in self.TRUE_VALUES:
            return True
        if normalized in self.FALSE_VALUES:
            return False
        raise ImproperlyConfigured(
            f"{key} must be one of {sorted(self.TRUE_VALUES | self.FALSE_VALUES)}."
        )

    def get_float(self, key: str, default: float) -> float:
        raw_value = self.environ.get(key)
        if raw_value is None:
            return default
        try:
            return float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ImproperlyConfigured(f"{key} must be a valid float.") from exc

    def get_list(self, key: str, default: Iterable[str] = ()) -> list[str]:
        raw_value = self.environ.get(key)
        if raw_value is None:
            return list(default)
        return [item.strip() for item in str(raw_value).split(",") if item.strip()]

    def get_path(self, key: str, default: Path, base_dir: Path) -> Path:
        raw_value = self.environ.get(key)
        if raw_value is None:
            return default

        path = Path(str(raw_value).strip())
        return path if path.is_absolute() else (base_dir / path).resolve()


@dataclass(frozen=True)
class RuntimeConfig:
    secret_key: str
    debug: bool
    allowed_hosts: list[str]
    csrf_trusted_origins: list[str]
    static_url: str
    static_root: Path
    sqlite_path: Path
    database_url: str
    secure_ssl_redirect: bool
    session_cookie_secure: bool
    csrf_cookie_secure: bool
    trust_x_forwarded_proto: bool
    enable_remote_fetch: bool
    enable_llm: bool
    anthropic_api_key: str
    anthropic_model: str
    anthropic_input_cost_per_mtok: float
    anthropic_output_cost_per_mtok: float
    http_timeout_seconds: float
    log_level: str
    rate_limit_enabled: bool
    max_request_body_bytes: int
    trending_cache_ttl_seconds: int
    benchmark_cache_ttl_seconds: int
    leadlag_cache_ttl_seconds: int
    leadlag_default_poll_seconds: int
    leadlag_default_trade_horizon_seconds: int
    mlflow_tracking_uri: str
    mlflow_experiment: str
    kalshi_api_base: str
    kalshi_ws_url: str
    kalshi_demo_api_base: str
    kalshi_access_key_id: str
    kalshi_private_key_path: Path
    polymarket_ws_url: str


def build_runtime_config(base_dir: Path) -> RuntimeConfig:
    load_dotenv(base_dir / ".env")
    env = EnvReader()

    debug = env.get_bool("DJANGO_DEBUG", True)
    secret_key = env.get_str("DJANGO_SECRET_KEY", DEFAULT_SECRET_KEY)
    if not debug and secret_key == DEFAULT_SECRET_KEY:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY must be set explicitly when DJANGO_DEBUG=0."
        )

    static_url = env.get_str("DJANGO_STATIC_URL", "/static/")
    if not static_url.endswith("/"):
        static_url = f"{static_url}/"

    return RuntimeConfig(
        secret_key=secret_key,
        debug=debug,
        allowed_hosts=env.get_list("DJANGO_ALLOWED_HOSTS", ["127.0.0.1", "localhost"]),
        csrf_trusted_origins=env.get_list("DJANGO_CSRF_TRUSTED_ORIGINS", []),
        static_url=static_url,
        static_root=env.get_path("DJANGO_STATIC_ROOT", base_dir / "staticfiles", base_dir),
        sqlite_path=env.get_path("DJANGO_SQLITE_PATH", base_dir / "db.sqlite3", base_dir),
        database_url=env.get_str("DATABASE_URL", ""),
        secure_ssl_redirect=env.get_bool("DJANGO_SECURE_SSL_REDIRECT", False),
        session_cookie_secure=env.get_bool("DJANGO_SESSION_COOKIE_SECURE", not debug),
        csrf_cookie_secure=env.get_bool("DJANGO_CSRF_COOKIE_SECURE", not debug),
        trust_x_forwarded_proto=env.get_bool("DJANGO_TRUST_X_FORWARDED_PROTO", False),
        enable_remote_fetch=env.get_bool("CHAOSWING_ENABLE_REMOTE_FETCH", True),
        enable_llm=env.get_bool("CHAOSWING_ENABLE_LLM", False),
        anthropic_api_key=env.get_str("ANTHROPIC_API_KEY", ""),
        anthropic_model=env.get_str("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        anthropic_input_cost_per_mtok=env.get_float("CHAOSWING_ANTHROPIC_INPUT_COST_PER_MTOK", 0.0),
        anthropic_output_cost_per_mtok=env.get_float("CHAOSWING_ANTHROPIC_OUTPUT_COST_PER_MTOK", 0.0),
        http_timeout_seconds=env.get_float("CHAOSWING_HTTP_TIMEOUT_SECONDS", 8.0),
        log_level=env.get_str("CHAOSWING_LOG_LEVEL", "INFO").upper(),
        rate_limit_enabled=env.get_bool("CHAOSWING_RATE_LIMIT_ENABLED", True),
        max_request_body_bytes=int(env.get_float("CHAOSWING_MAX_REQUEST_BODY_BYTES", 1_048_576)),
        trending_cache_ttl_seconds=int(env.get_float("CHAOSWING_TRENDING_CACHE_TTL", 300)),
        benchmark_cache_ttl_seconds=int(env.get_float("CHAOSWING_BENCHMARK_CACHE_TTL", 120)),
        leadlag_cache_ttl_seconds=int(env.get_float("CHAOSWING_LEADLAG_CACHE_TTL", 60)),
        leadlag_default_poll_seconds=int(env.get_float("CHAOSWING_LEADLAG_POLL_SECONDS", 5)),
        leadlag_default_trade_horizon_seconds=int(
            env.get_float("CHAOSWING_LEADLAG_TRADE_HORIZON_SECONDS", 180)
        ),
        mlflow_tracking_uri=env.get_str(
            "CHAOSWING_MLFLOW_TRACKING_URI",
            "sqlite:///mlflow.db",
        ),
        mlflow_experiment=env.get_str("CHAOSWING_MLFLOW_EXPERIMENT", "ChaosWing"),
        kalshi_api_base=env.get_str(
            "CHAOSWING_KALSHI_API_BASE",
            "https://api.elections.kalshi.com/trade-api/v2",
        ),
        kalshi_ws_url=env.get_str(
            "CHAOSWING_KALSHI_WS_URL",
            "wss://api.elections.kalshi.com/trade-api/ws/v2",
        ),
        kalshi_demo_api_base=env.get_str(
            "CHAOSWING_KALSHI_DEMO_API_BASE",
            "https://demo-api.kalshi.co/trade-api/v2",
        ),
        kalshi_access_key_id=env.get_str("CHAOSWING_KALSHI_ACCESS_KEY_ID", ""),
        kalshi_private_key_path=env.get_path(
            "CHAOSWING_KALSHI_PRIVATE_KEY_PATH",
            base_dir / "secrets" / "kalshi_api_key.pem",
            base_dir,
        ),
        polymarket_ws_url=env.get_str(
            "CHAOSWING_POLYMARKET_WS_URL",
            "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        ),
    )
