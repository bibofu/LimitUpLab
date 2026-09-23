"""Decode only a single finish call's top-level answer as its JSON arrives."""

import json
from collections.abc import Callable, Mapping, Sequence


class FinishAnswerStream:
    def __init__(self, on_delta: Callable[[str], None],
                 on_invalid: Callable[[], None] | None = None):
        self.on_delta, self.on_invalid = on_delta, on_invalid
        self.disabled = False
        self._index = None
        self._name = ""
        self._buffer = ""
        self._pos = 0
        self._state = "start"
        self._key = ""
        self._answered = False
        self._decoder = json.JSONDecoder(parse_constant=self._reject_constant)

    @staticmethod
    def _reject_constant(value: str):
        raise ValueError(f"Non-JSON constant: {value}")

    def invalidate(self) -> None:
        if not self.disabled:
            self.disabled = True
            if self.on_invalid:
                self.on_invalid()

    def feed(self, chunks: Sequence[Mapping]) -> None:
        if self.disabled:
            return
        # Inspect the entire batch before emitting any part of parallel calls.
        indexes = {chunk.get("index") or 0 for chunk in chunks}
        if self._index is not None:
            indexes.add(self._index)
        if len(indexes) > 1:
            self.invalidate()
            return
        for chunk in chunks:
            self._index = chunk.get("index") or 0
            self._name += chunk.get("name") or ""
            self._buffer += chunk.get("args") or ""
        if self._name == "finish":
            self._drain()
        elif not "finish".startswith(self._name):
            self.invalidate()

    def _drain(self) -> None:
        output = []
        while self._pos < len(self._buffer) and not self.disabled:
            ch = self._buffer[self._pos]
            if self._state == "answer":
                if ch == '"':
                    self._pos += 1
                    self._state = "comma"
                    continue
                decoded = self._character()
                if decoded is None:
                    break
                output.append(decoded)
                continue
            if ch in " \t\r\n":
                self._pos += 1
                continue
            if self._state == "start":
                if ch != "{":
                    self.invalidate()
                    break
                self._pos += 1
                self._state = "first_key"
            elif self._state in ("first_key", "key"):
                if ch == "}" and self._state == "first_key":
                    self._pos += 1
                    self._state = "done"
                    continue
                if ch != '"':
                    self.invalidate()
                    break
                parsed = self._value()
                if parsed is None:
                    break
                self._key, self._pos = parsed
                self._state = "colon"
            elif self._state == "colon":
                if ch != ":":
                    self.invalidate()
                    break
                self._pos += 1
                self._state = "value"
            elif self._state == "value":
                if self._key == "answer":
                    if ch != '"' or self._answered:
                        self.invalidate()
                        break
                    self._answered = True
                    self._pos += 1
                    self._state = "answer"
                else:
                    parsed = self._value()
                    if parsed is None:
                        break
                    tail = self._buffer[parsed[1]:].lstrip(" \t\r\n")
                    if not tail or tail[0] not in ",}":
                        break  # A number or literal may continue in the next chunk.
                    self._pos = parsed[1]
                    self._state = "comma"
            elif self._state == "comma" and ch in ",}":
                self._pos += 1
                self._state = "key" if ch == "," else "done"
            else:
                self.invalidate()
        if output and not self.disabled:
            self.on_delta("".join(output))

    def _value(self):
        try:
            return self._decoder.raw_decode(self._buffer, self._pos)
        except (ValueError, RecursionError):
            return None  # Incomplete values are never repaired or guessed.

    def _character(self) -> str | None:
        pos, data = self._pos, self._buffer
        ch = data[pos]
        width = 1
        if ch == "\\":
            if len(data) < pos + 2:
                return None
            escape = data[pos + 1]
            if escape == "u":
                if len(data) < pos + 6:
                    return None
                digits = data[pos + 2:pos + 6]
                if any(c not in "0123456789abcdefABCDEF" for c in digits):
                    self.invalidate()
                    return None
                code, width = int(digits, 16), 6
                if 0xD800 <= code <= 0xDBFF:
                    if not "\\u".startswith(data[pos + 6:pos + 8]):
                        self.invalidate()
                        return None
                    if len(data) < pos + 12:
                        return None
                    low_digits = data[pos + 8:pos + 12]
                    if (data[pos + 6:pos + 8] != "\\u"
                            or any(c not in "0123456789abcdefABCDEF" for c in low_digits)
                            or not 0xDC00 <= int(low_digits, 16) <= 0xDFFF):
                        self.invalidate()
                        return None
                    code = 0x10000 + ((code - 0xD800) << 10) + int(low_digits, 16) - 0xDC00
                    width = 12
                elif 0xDC00 <= code <= 0xDFFF:
                    self.invalidate()
                    return None
                ch = chr(code)
            else:
                escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
                           "n": "\n", "r": "\r", "t": "\t"}
                if escape not in escapes:
                    self.invalidate()
                    return None
                ch, width = escapes[escape], 2
        elif ord(ch) < 0x20 or 0xD800 <= ord(ch) <= 0xDFFF:
            self.invalidate()
            return None
        self._pos += width
        return ch
