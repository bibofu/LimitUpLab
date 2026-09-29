import multiprocessing
import os
from pathlib import Path
import time
import unittest
from tempfile import TemporaryDirectory

import pandas as pd

from app.collectors.process_timeout import run_in_killable_process


def _wait_forever(started_path: str) -> None:
    Path(started_path).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(60)


def _large_frame() -> pd.DataFrame:
    return pd.DataFrame({"value": [f"{index:05d}" + "x" * 200 for index in range(20_000)]})


def _provider_error() -> None:
    raise ConnectionError("test provider unavailable")


class CollectorProcessTimeoutTest(unittest.TestCase):
    def test_timeout_terminates_and_reaps_running_provider(self) -> None:
        before = {child.pid for child in multiprocessing.active_children()}
        with TemporaryDirectory() as directory:
            started_path = Path(directory) / "started"
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "timed out after 5 seconds"):
                run_in_killable_process(_wait_forever, str(started_path), timeout_seconds=5)
            self.assertLess(time.monotonic() - started, 12)
            self.assertTrue(started_path.is_file(), "worker should enter the blocked provider call")
        after = {child.pid for child in multiprocessing.active_children()}
        self.assertEqual(after, before)

    def test_large_dataframe_returns_without_pipe_backpressure(self) -> None:
        result = run_in_killable_process(_large_frame, timeout_seconds=20)
        self.assertEqual(len(result), 20_000)
        self.assertEqual(result.iloc[-1]["value"], "19999" + "x" * 200)

    def test_provider_exception_is_reported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ConnectionError: test provider unavailable"):
            run_in_killable_process(_provider_error, timeout_seconds=20)

    def test_worker_crash_is_reported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exited with code 7"):
            run_in_killable_process(os._exit, 7, timeout_seconds=20)

    def test_worker_exit_without_result_is_reported(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exited without a result"):
            run_in_killable_process(os._exit, 0, timeout_seconds=20)

    def test_invalid_timeout_is_rejected_before_start(self) -> None:
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                run_in_killable_process(time.sleep, 60, timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
