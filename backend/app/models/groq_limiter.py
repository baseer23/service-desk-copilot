"""Shared model-specific Groq capacity queue, driven by account headers.

Groq's request headers describe DAILY requests, while token headers describe
MINUTE tokens. RPM and TPD must come from the account's actual Limits page;
zero means unknown, never an invented quota. Unknown concurrency is serialized.
Only rejected 429 model calls are retried. No tools execute inside this queue.
"""

import asyncio
import math
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .provider import LLMError
from .provider_routing import model_event


def duration_seconds(value: str | None) -> float | None:
    """Parse Retry-After seconds/HTTP dates and Groq's 2m59.56s durations."""
    if not value:
        return None
    value = value.strip()
    try:
        number = float(value)
        return max(0, number) if math.isfinite(number) else None
    except ValueError:
        pass
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", value)
    if parts and "".join(number + unit for number, unit in parts) == value:
        return sum(
            float(number) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
            for number, unit in parts
        )
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class Budget:
    limit: int
    remaining: int
    reset_at: float
    period: float


class GroqRateLimiter:
    def __init__(self, settings, *, clock=time.monotonic, sleeper=asyncio.sleep):
        self.clock, self.sleep = clock, sleeper
        self.timeout = settings.groq_queue_timeout_sec
        self.semaphore = asyncio.Semaphore(settings.groq_max_concurrent_requests)
        self.lock = asyncio.Lock()
        self.cooldown_until = 0.0
        self.budgets: dict[str, Budget] = {}
        self.history: deque[tuple[float, int, str]] = deque()
        self.configured = {
            "rpm": (settings.groq_requests_per_minute, 60, False),
            "rpd": (settings.groq_requests_per_day, 86400, False),
            "tpm": (settings.groq_tokens_per_minute, 60, True),
            "tpd": (settings.groq_tokens_per_day, 86400, True),
        }

    def token_capacity(self) -> int | None:
        """Return only token limits learned from Groq or explicitly configured."""
        limits = [budget.limit for kind, budget in self.budgets.items() if kind == "tpm"]
        limits.extend(
            limit
            for limit, _seconds, token_limit in self.configured.values()
            if token_limit and limit
        )
        return min(limits) if limits else None

    async def observe(self, headers, *, retry: bool = False, fallback_delay: float = 1) -> None:
        headers = {key.lower(): value for key, value in headers.items()}
        now = self.clock()
        async with self.lock:
            for suffix, kind, period in (("requests", "rpd", 86400), ("tokens", "tpm", 60)):
                try:
                    limit = int(headers[f"x-ratelimit-limit-{suffix}"])
                    remaining = int(headers[f"x-ratelimit-remaining-{suffix}"])
                    reset = duration_seconds(headers.get(f"x-ratelimit-reset-{suffix}"))
                    if limit <= 0 or remaining < 0:
                        continue
                    # Missing reset information must never make an exhausted
                    # bucket look unlimited; use its documented period.
                    self.budgets[kind] = Budget(
                        limit,
                        min(remaining, limit),
                        now + (reset if reset is not None else period),
                        period,
                    )
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
            if retry:
                delay = duration_seconds(headers.get("retry-after"))
                self.cooldown_until = max(
                    self.cooldown_until, now + max(delay or 0, fallback_delay)
                )

    def _delay(self, tokens: int) -> float:
        now = self.clock()
        delay = max(0, self.cooldown_until - now)
        while self.history and self.history[0][0] <= now - 86400:
            self.history.popleft()
        for kind, budget in self.budgets.items():
            needed = tokens if kind == "tpm" else 1
            if needed > budget.limit:
                raise LLMError(
                    "Groq request exceeds this model's token capacity; shorten the request"
                )
            if now >= budget.reset_at:
                budget.remaining = budget.limit
                budget.reset_at = now + budget.period
            if needed > budget.remaining:
                delay = max(delay, budget.reset_at - now)
        for _, (limit, seconds, token_limit) in self.configured.items():
            if not limit:
                continue
            needed = tokens if token_limit else 1
            if needed > limit:
                raise LLMError(
                    "Groq request exceeds the configured account capacity; shorten the request"
                )
            recent = [
                (at, count if token_limit else 1)
                for at, count, _ in self.history
                if at > now - seconds
            ]
            used = sum(count for _, count in recent)
            if used + needed > limit:
                for at, count in recent:
                    used -= count
                    if used + needed <= limit:
                        delay = max(delay, at + seconds - now)
                        break
        return delay

    @asynccontextmanager
    async def slot(self, tokens: int, stage: str):
        started = self.clock()
        if self.semaphore.locked():
            await model_event(
                kind="llm_capacity",
                status="waiting",
                provider="groq",
                stage=stage,
                summary="Waiting for Groq request capacity",
            )
        try:
            await asyncio.wait_for(self.semaphore.acquire(), timeout=self.timeout)
        except TimeoutError:
            raise LLMError("Groq capacity queue timed out; try again later") from None
        try:
            while True:
                async with self.lock:
                    delay = self._delay(tokens)
                    if delay <= 0:
                        for kind, budget in self.budgets.items():
                            budget.remaining -= tokens if kind == "tpm" else 1
                        reservation = uuid.uuid4().hex
                        self.history.append((self.clock(), tokens, reservation))
                        break
                if self.clock() - started + delay > self.timeout:
                    await model_event(
                        kind="llm_capacity",
                        status="waiting",
                        provider="groq",
                        stage=stage,
                        wait_seconds=round(delay, 2),
                        summary="Groq capacity is exhausted; retry after the reset",
                    )
                    raise LLMError(
                        "Groq capacity is exhausted; wait for the account limit to reset"
                    )
                await model_event(
                    kind="llm_capacity",
                    status="waiting",
                    provider="groq",
                    stage=stage,
                    wait_seconds=round(delay, 2),
                    summary="Waiting for Groq rate-limit capacity",
                )
                await self.sleep(delay)
            await model_event(
                kind="llm_capacity",
                status="running",
                provider="groq",
                stage=stage,
                summary="Groq request started",
            )
            yield reservation
        finally:
            self.semaphore.release()

    async def settle(self, reservation: str, actual: int) -> None:
        """Settle our configured rolling limits using actual returned counters."""
        async with self.lock:
            self.history = deque(
                (at, max(0, actual) if key == reservation else estimate, key)
                for at, estimate, key in self.history
            )
