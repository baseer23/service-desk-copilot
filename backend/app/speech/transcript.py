"""Recognition-event identity and a deliberately narrow spoken Stop command."""

import re
import time


def standalone_stop(text: str) -> bool:
    return bool(re.fullmatch(r"\s*stop[.!?,;:]*\s*", text, re.IGNORECASE))


class Transcript:
    def __init__(self):
        self.seen: set[tuple[int, str]] = set()
        self.last_text = ""
        self.last_generation = -1
        self.last_at = 0.0

    def accept(self, generation: int, result_id: str, text: str) -> bool:
        return self.finalize(generation, result_id, text) is not None

    def finalize(self, generation: int, result_id: str, text: str) -> str | None:
        """Deduplicate callback repeats and the short replay after reconnect only.

        Identical words spoken later in the same recognizer are legitimate speech.
        Raw audio is retained only in a bounded in-memory reconnect buffer.
        """
        identity = (generation, result_id)
        if identity in self.seen:
            return None
        self.seen.add(identity)
        normalized = re.sub(r"\W+", " ", text.casefold()).strip()
        now = time.monotonic()
        reconnect = generation != self.last_generation and now - self.last_at < 5
        duplicate = reconnect and normalized == self.last_text
        if reconnect and not duplicate:
            previous = self.last_text.split()
            incoming = list(re.finditer(r"\w+", text, re.UNICODE))
            words = [match.group().casefold() for match in incoming]
            for size in range(min(len(previous), len(words)), 0, -1):
                if previous[-size:] == words[:size] and (size > 1 or len(words[0]) > 4):
                    text = text[incoming[size - 1].end() :].lstrip(" \t.,!?;:-")
                    normalized = re.sub(r"\W+", " ", text.casefold()).strip()
                    break
        self.last_generation = generation
        self.last_text, self.last_at = normalized, now
        return text if normalized and not duplicate else None
