"""Deliver provisional answer text while the committed answer stays authoritative.

The model streams one answer at a time. Text from tool-selection rounds, a
length-truncated draft, a cancelled run or a draft that trips the compliance
signal gate is withdrawn with ``answer_reset``; the transport never publishes a
draft as a receipt. Chunks are coalesced so one answer costs tens of durable
events rather than hundreds.
"""

from __future__ import annotations

from time import monotonic

from app.agent_output_sanitizer import sanitize_agent_answer
from app.agents.runner.guardrails import requires_compliance_review

FLUSH_CHARS = 120
FLUSH_INTERVAL_SECONDS = 0.2
RESET_MESSAGE = "正在重新整理回答"


class AnswerDelivery:
    """Emit a bounded, resettable candidate answer for one run."""

    def __init__(self, emit=None, *, revision_base: int = 0):
        self.emit = emit
        self.revision = max(0, int(revision_base or 0))
        self.started = False
        self.blocked = False
        self.sent = ""
        self.buffer = ""
        self.last_flush = 0.0

    def begin_round(self) -> None:
        """A new model round may stream again after a withdrawn round."""

        self.blocked = False

    def feed(self, text: str) -> None:
        """Accept streamed text without letting a slow consumer block the model."""

        if not self.emit or self.blocked or not text:
            return
        self.buffer += text
        if len(self.buffer) >= FLUSH_CHARS or monotonic() - self.last_flush >= FLUSH_INTERVAL_SECONDS:
            self.flush()

    def flush(self) -> None:
        """Publish the unsent tail, withdrawing text that cannot stay consistent."""

        if not self.emit or not self.buffer:
            return
        full = sanitize_agent_answer(self.sent + self.buffer)
        if requires_compliance_review(full):
            # Never show an actionable sentence, even transiently. The committed
            # answer still goes through the semantic reviewer on its own path.
            self.reset("回答正在接受合规核验")
            self.blocked = True
            return
        if self.started and not full.startswith(self.sent):
            # The sanitizer rewrote text that was already visible (an internal
            # identifier split across flushes). Rebuild the draft under a new
            # revision so the client never keeps a stale prefix.
            self.reset("正在校订回答")
            if full:
                self._start()
                self._delta(full)
            self.sent = full
        else:
            if not self.started and full:
                self._start()
            suffix = full[len(self.sent):]
            if suffix:
                self._delta(suffix)
            self.sent = full
        self.buffer = ""
        self.last_flush = monotonic()

    def finish(self) -> None:
        """Flush the final model round without withdrawing its text."""

        self.flush()

    def reset(self, message: str = RESET_MESSAGE) -> None:
        """Withdraw the visible draft; the next start opens a new revision."""

        if self.emit and self.started:
            self._emit("answer_reset", {"revision": self.revision, "message": message})
        self.started = False
        self.sent = ""
        self.buffer = ""
        self.last_flush = 0.0

    def withdraw(self, message: str = RESET_MESSAGE) -> None:
        """Withdraw a round that will not become the committed answer."""

        self.reset(message)

    def _start(self) -> None:
        self.revision += 1
        self.started = True
        self._emit("answer_start", {"revision": self.revision, "provisional": True})

    def _delta(self, text: str) -> None:
        self._emit("answer_delta", {"offset": len(self.sent), "delta": text})

    def _emit(self, kind: str, payload: dict) -> None:
        """Streaming is fail-open: a broken emitter must never fail the answer."""

        if not self.emit:
            return
        try:
            self.emit(kind, payload)
        except Exception:  # noqa: BLE001 - telemetry must not block research
            self.emit = None
