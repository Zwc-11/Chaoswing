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
