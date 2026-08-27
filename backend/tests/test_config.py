import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.config import (
    DEFAULT_CORS_ORIGINS,
    PROXY_ENV_NAMES,
    clear_unreachable_local_proxy,
    configured_cors_origins,
    configure_runtime_environment,
    hydrate_windows_environment,
    load_local_env,
    replace_proxy_environment,
)


TEST_TMP_ROOT = Path(os.getenv("LIMITUPLAB_TEST_TMP", Path(__file__).resolve().parents[1]))


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_env = dict(os.environ)
        TEST_TMP_ROOT.mkdir(exist_ok=True)
        self._test_dir = TEST_TMP_ROOT / f"config-test-{uuid4().hex}"
        self._test_dir.mkdir()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._original_env)
        shutil.rmtree(self._test_dir, ignore_errors=True)

    def test_loads_env_file_without_overriding_existing_values(self) -> None:
        for name in (
            "LIMITUPLAB_LLM_ENABLED",
            "LIMITUPLAB_LLM_MODEL",
            "LIMITUPLAB_LLM_BASE_URL",
        ):
            os.environ.pop(name, None)
        env_path = self._test_dir / ".env"
        env_path.write_text(
            "\n".join(
                [
                    "LIMITUPLAB_LLM_ENABLED=true",
                    "LIMITUPLAB_LLM_MODEL=from-file",
                    'LIMITUPLAB_LLM_BASE_URL="https://api.example.com"',
                ]
            ),
            encoding="utf-8",
        )

        os.environ["LIMITUPLAB_LLM_MODEL"] = "from-process"
        loaded = load_local_env(env_path)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(os.getenv("LIMITUPLAB_LLM_ENABLED"), "true")
        self.assertEqual(os.getenv("LIMITUPLAB_LLM_MODEL"), "from-process")
        self.assertEqual(os.getenv("LIMITUPLAB_LLM_BASE_URL"), "https://api.example.com")

    def test_proxy_alias_sets_standard_proxy_variables(self) -> None:
        env_path = self._test_dir / ".env"
        env_path.write_text(
            "LIMITUPLAB_PROXY_URL=http://127.0.0.1:17891\n",
            encoding="utf-8",
        )

        load_local_env(env_path)

        self.assertEqual(os.getenv("HTTP_PROXY"), "http://127.0.0.1:17891")
        self.assertEqual(os.getenv("HTTPS_PROXY"), "http://127.0.0.1:17891")
        self.assertEqual(os.getenv("ALL_PROXY"), "http://127.0.0.1:17891")

    @patch("app.config._read_windows_environment_value", return_value="secret-value")
    def test_hydrates_missing_windows_environment_without_overwriting_process(
        self,
        read_value,
    ) -> None:
        os.environ.pop("TEST_WINDOWS_SECRET", None)

        loaded = hydrate_windows_environment(["TEST_WINDOWS_SECRET"])

        self.assertEqual(loaded, ["TEST_WINDOWS_SECRET"])
        self.assertEqual(os.getenv("TEST_WINDOWS_SECRET"), "secret-value")
        read_value.assert_called_once_with("TEST_WINDOWS_SECRET")

        os.environ["TEST_WINDOWS_SECRET"] = "process-value"
        loaded_again = hydrate_windows_environment(["TEST_WINDOWS_SECRET"])
        self.assertEqual(loaded_again, [])
        self.assertEqual(os.getenv("TEST_WINDOWS_SECRET"), "process-value")

    def test_replace_proxy_environment_removes_all_inherited_variants(self) -> None:
        for name in PROXY_ENV_NAMES:
            os.environ[name] = "http://localhost:65535"

        replace_proxy_environment("http://127.0.0.1:17891")

        self.assertTrue(
            all(
                os.getenv(name) == "http://127.0.0.1:17891"
                for name in PROXY_ENV_NAMES
            )
        )

        replace_proxy_environment()
        self.assertTrue(all(name not in os.environ for name in PROXY_ENV_NAMES))

    def test_cors_origins_default_to_local_development(self) -> None:
        os.environ.pop("LIMITUPLAB_CORS_ORIGINS", None)

        self.assertEqual(configured_cors_origins(), list(DEFAULT_CORS_ORIGINS))

    def test_cors_origins_are_trimmed_deduplicated_and_normalized(self) -> None:
        os.environ["LIMITUPLAB_CORS_ORIGINS"] = (
            "https://example.com/, https://www.example.com, https://example.com"
        )

        self.assertEqual(
            configured_cors_origins(),
            ["https://example.com", "https://www.example.com"],
        )

    def test_cors_origins_reject_wildcard_with_credentials(self) -> None:
        os.environ["LIMITUPLAB_CORS_ORIGINS"] = "*"

        with self.assertRaisesRegex(ValueError, "explicit origins"):
            configured_cors_origins()

    @patch("app.config._proxy_endpoint_reachable", return_value=False)
    def test_runtime_configuration_enables_llm_and_clears_dead_proxy(
        self,
        _reachable,
    ) -> None:
        os.environ["DEEPSEEK_API_KEY"] = "test-secret"
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:65534"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:65534"

        configure_runtime_environment(self._test_dir / "missing.env")

        self.assertEqual(os.getenv("LIMITUPLAB_LLM_ENABLED"), "true")
        self.assertEqual(os.getenv("LIMITUPLAB_LLM_BASE_URL"), "https://api.deepseek.com")
        self.assertEqual(os.getenv("LIMITUPLAB_LLM_MODEL"), "deepseek-v4-flash")
        self.assertTrue(all(name not in os.environ for name in PROXY_ENV_NAMES))

    @patch("app.config._proxy_endpoint_reachable", return_value=True)
    def test_dead_proxy_cleanup_leaves_valid_proxy_untouched(
        self,
        _reachable,
    ) -> None:
        for name in PROXY_ENV_NAMES:
            os.environ.pop(name, None)
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"

        removed = clear_unreachable_local_proxy()

        self.assertFalse(removed)
        self.assertEqual(os.getenv("HTTP_PROXY"), "http://127.0.0.1:7890")


if __name__ == "__main__":
    unittest.main()
