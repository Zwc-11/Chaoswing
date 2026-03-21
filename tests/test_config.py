from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured

from chaoswing.config import DEFAULT_SECRET_KEY, build_runtime_config, load_dotenv


class DotenvLoaderTests(TestCase):
    def test_load_dotenv_keeps_existing_shell_values(self):
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "DJANGO_DEBUG=0\n"
                "CHAOSWING_ENABLE_LLM=1\n",
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {"DJANGO_DEBUG": "1"},
                clear=True,
            ):
                load_dotenv(env_path)
                self.assertEqual("1", os.environ["DJANGO_DEBUG"])
                self.assertEqual("1", os.environ["CHAOSWING_ENABLE_LLM"])


class RuntimeConfigTests(TestCase):
    def test_build_runtime_config_reads_env_backed_runtime(self):
        with TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            with patch.dict(
                "os.environ",
                {
                    "DJANGO_SECRET_KEY": "test-secret",
                    "DJANGO_DEBUG": "0",
                    "DJANGO_ALLOWED_HOSTS": "localhost,example.com",
                    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://example.com,https://app.example.com",
                    "DJANGO_STATIC_ROOT": "collected-static",
                    "DJANGO_SQLITE_PATH": "data/chaoswing.sqlite3",
                    "DJANGO_SECURE_SSL_REDIRECT": "1",
                    "DJANGO_SESSION_COOKIE_SECURE": "1",
                    "DJANGO_CSRF_COOKIE_SECURE": "1",
                    "DJANGO_TRUST_X_FORWARDED_PROTO": "1",
                    "CHAOSWING_ENABLE_REMOTE_FETCH": "0",
                    "CHAOSWING_ENABLE_LLM": "1",
                    "CHAOSWING_HTTP_TIMEOUT_SECONDS": "12.5",
                    "CHAOSWING_LOG_LEVEL": "debug",
                    "ANTHROPIC_API_KEY": "secret-key",
                    "ANTHROPIC_MODEL": "claude-sonnet-4-6",
                    "CHAOSWING_ANTHROPIC_INPUT_COST_PER_MTOK": "3",
                    "CHAOSWING_ANTHROPIC_OUTPUT_COST_PER_MTOK": "15",
                    "CHAOSWING_LEADLAG_CACHE_TTL": "45",
                    "CHAOSWING_LEADLAG_POLL_SECONDS": "7",
                    "CHAOSWING_LEADLAG_TRADE_HORIZON_SECONDS": "240",
                    "CHAOSWING_KALSHI_API_BASE": "https://api.kalshi.test/trade-api/v2",
                    "CHAOSWING_KALSHI_WS_URL": "wss://api.kalshi.test/ws/v2",
                    "CHAOSWING_KALSHI_DEMO_API_BASE": "https://demo-api.kalshi.test/trade-api/v2",
                    "CHAOSWING_KALSHI_ACCESS_KEY_ID": "kalshi-key-id",
                    "CHAOSWING_KALSHI_PRIVATE_KEY_PATH": "secrets/kalshi.pem",
                    "CHAOSWING_POLYMARKET_WS_URL": "wss://polymarket.test/ws/market",
                },
                clear=True,
            ):
                runtime = build_runtime_config(base_dir)

            self.assertEqual("test-secret", runtime.secret_key)
            self.assertFalse(runtime.debug)
            self.assertEqual(["localhost", "example.com"], runtime.allowed_hosts)
            self.assertEqual(
                ["https://example.com", "https://app.example.com"],
                runtime.csrf_trusted_origins,
            )
            self.assertEqual((base_dir / "collected-static").resolve(), runtime.static_root)
            self.assertEqual((base_dir / "data/chaoswing.sqlite3").resolve(), runtime.sqlite_path)
            self.assertTrue(runtime.secure_ssl_redirect)
            self.assertTrue(runtime.session_cookie_secure)
            self.assertTrue(runtime.csrf_cookie_secure)
            self.assertTrue(runtime.trust_x_forwarded_proto)
            self.assertFalse(runtime.enable_remote_fetch)
            self.assertTrue(runtime.enable_llm)
            self.assertEqual(12.5, runtime.http_timeout_seconds)
            self.assertEqual("DEBUG", runtime.log_level)
            self.assertEqual("secret-key", runtime.anthropic_api_key)
            self.assertEqual(3.0, runtime.anthropic_input_cost_per_mtok)
            self.assertEqual(15.0, runtime.anthropic_output_cost_per_mtok)
            self.assertEqual(45, runtime.leadlag_cache_ttl_seconds)
            self.assertEqual(7, runtime.leadlag_default_poll_seconds)
            self.assertEqual(240, runtime.leadlag_default_trade_horizon_seconds)
            self.assertEqual("https://api.kalshi.test/trade-api/v2", runtime.kalshi_api_base)
            self.assertEqual("wss://api.kalshi.test/ws/v2", runtime.kalshi_ws_url)
            self.assertEqual("https://demo-api.kalshi.test/trade-api/v2", runtime.kalshi_demo_api_base)
            self.assertEqual("kalshi-key-id", runtime.kalshi_access_key_id)
            self.assertEqual((base_dir / "secrets/kalshi.pem").resolve(), runtime.kalshi_private_key_path)
            self.assertEqual("wss://polymarket.test/ws/market", runtime.polymarket_ws_url)

    def test_build_runtime_config_requires_real_secret_when_debug_is_off(self):
        with TemporaryDirectory() as tmpdir:
            with patch.dict(
                "os.environ",
                {
                    "DJANGO_DEBUG": "0",
                    "DJANGO_SECRET_KEY": DEFAULT_SECRET_KEY,
                },
                clear=True,
            ):
                with self.assertRaises(ImproperlyConfigured):
                    build_runtime_config(Path(tmpdir))
