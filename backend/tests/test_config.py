import os
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from app.config import load_local_env


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


if __name__ == "__main__":
    unittest.main()
