"""Bound provider calls that do not expose a reliable network timeout."""

from collections.abc import Callable
from math import isfinite
import multiprocessing
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
from typing import Any, TypeVar


Result = TypeVar("Result")
_STOP_TIMEOUT_SECONDS = 2.0


def _write_result(
    function: Callable[..., Any], args: tuple[Any, ...], result_path: str,
) -> None:
    """Run in a fresh interpreter; only this child writes its private result."""

    try:
        result = function(*args)
        with open(result_path, "wb") as output:
            pickle.dump((True, result), output, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as error:  # noqa: BLE001 - report provider and serialization errors
        with open(result_path, "wb") as output:
            pickle.dump((False, f"{type(error).__name__}: {error}"), output)


def run_in_killable_process(
    function: Callable[..., Result], *args: Any, timeout_seconds: float,
) -> Result:
    """Run a picklable function with a deadline, then terminate/reap on timeout.

    Spawn avoids inheriting threads and network locks on Linux and also works
    on Windows. A private result file avoids pipe/queue backpressure while the
    parent waits for a large DataFrame to finish serializing. The deadline
    includes interpreter startup, provider work and result serialization.
    """

    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Provider timeout must be a finite number greater than zero")

    with TemporaryDirectory(prefix="limituplab-provider-") as directory:
        result_path = Path(directory) / "result.pickle"
        process = multiprocessing.get_context("spawn").Process(
            target=_write_result,
            args=(function, args, str(result_path)),
            name="limituplab-provider",
            daemon=True,
        )
        try:
            process.start()
            process.join(timeout_seconds)
            if process.is_alive():
                raise TimeoutError(f"Provider call timed out after {timeout_seconds:g} seconds")
            if process.exitcode != 0:
                raise RuntimeError(f"Provider worker exited with code {process.exitcode}")
            if not result_path.is_file():
                raise RuntimeError("Provider worker exited without a result")
            # The private directory and pickle are created by this process and
            # its own worker; no remote pickle or user-supplied file is loaded.
            with result_path.open("rb") as source:
                succeeded, result = pickle.load(source)
            if not succeeded:
                raise RuntimeError(result)
            return result
        finally:
            if process.is_alive():
                process.terminate()
                process.join(_STOP_TIMEOUT_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_STOP_TIMEOUT_SECONDS)
            if process.is_alive():
                raise RuntimeError("Provider worker could not be terminated")
            process.close()
