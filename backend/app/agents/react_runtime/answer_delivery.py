"""Deliver provisional answer text while retaining an authoritative final gate."""

from app.services.prompt_security import contains_prompt_leak


class AnswerDelivery:
    def __init__(self, emit=None):
        self.emit = emit
        self.revision = 0
        self.started = False
        self.text = self.pending = ""
        self.blocked = False

    def append(self, delta):
        if not self.emit or not delta:
            return
        if not self.started:
            self.revision += 1
            self.started = True
            self.emit("answer_start", {
                "revision": self.revision, "answer_length": None,
                "stock_mentions": [], "provisional": True,
            })
        offset = len(self.text)
        self.text += delta
        self.emit("answer_delta", {"offset": offset, "delta": delta})

    def feed(self, delta):
        if not self.emit or self.blocked:
            return
        self.pending += delta
        if contains_prompt_leak(self.text + self.pending):
            self.reset()
            self.blocked = True
            return
        # The evidence table is server-rendered once its declaration is complete.
        # Hold the marker and following prose so no placeholder leaks or reorders.
        marker = "{{evidence_table}}"
        position = self.pending.find(marker)
        if position >= 0:
            self.append(self.pending[:position])
            self.pending = ""
            self.blocked = True
            return
        keep = next((size for size in range(min(len(self.pending), len(marker) - 1), 0, -1)
                     if self.pending.endswith(marker[:size])), 0)
        end = len(self.pending) - keep
        self.append(self.pending[:end])
        self.pending = self.pending[end:]

    def render(self, answer):
        """Fill deterministic table content before the final semantic review."""
        if not answer.startswith(self.text):
            self.reset()
        self.append(answer[len(self.text):])
        self.pending = ""

    def reset(self):
        if self.emit and self.started:
            self.emit("answer_reset", {"message": "正在重新整理回答"})
        self.started = False
        self.text = self.pending = ""
        self.blocked = False
