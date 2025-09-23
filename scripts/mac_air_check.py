#!/usr/bin/env python3
"""Quick MacBook Air benchmark comparing local and hosted generation."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests


PROMPT = "Give a one sentence status update: confirm DeskMate is ready."  # short, deterministic
LOG_PATH = Path("logs/mac-air-check.txt")
DEFAULT_BACKEND = os.getenv("SDC_API_BASE", "http://localhost:8000")
DEFAULT_OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434")


@dataclass
class LocalResult:
    model: Optional[str]
    first_token: Optional[float]
    total_time: Optional[float]
    message: str
    tokens: int = 0


@dataclass
class HostedResult:
    model: Optional[str]
    latency: Optional[float]
    message: str


def fetch_health() -> Dict[str, Any] | None:
    try:
        response = requests.get(f"{DEFAULT_BACKEND.rstrip('/')}/health", timeout=2)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def measure_local(model: str, host: str) -> LocalResult:
    url = f"{host.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": PROMPT,
        "stream": True,
        "options": {"temperature": 0, "num_predict": 128},
    }
    start = time.perf_counter()
    first_token: Optional[float] = None
    tokens = 0
    try:
        with requests.post(url, json=payload, stream=True, timeout=(5, 60)) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    return LocalResult(model, None, None, f"Local error: {data['error']}")
                if data.get("done"):
                    total = time.perf_counter() - start
                    return LocalResult(model, first_token, total, "ok", tokens=tokens)
                tokens += 1
                if first_token is None:
                    first_token = time.perf_counter() - start
    except requests.RequestException as exc:
        return LocalResult(model, None, None, f"Local request failed: {exc}")
    return LocalResult(model, None, None, "Local stream ended unexpectedly")


def measure_hosted(model: str, api_key: str | None, url: str) -> HostedResult:
    if not api_key:
        return HostedResult(model, None, "Hosted skipped: GROQ_API_KEY not set")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are DeskMate running in hosted fallback mode."},
            {"role": "user", "content": PROMPT},
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    start = time.perf_counter()
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        _ = response.json()
    except requests.RequestException as exc:
        return HostedResult(model, None, f"Hosted request failed: {exc}")
    return HostedResult(model, time.perf_counter() - start, "ok")


def format_local(result: LocalResult) -> str:
    if result.message != "ok" or not result.first_token or not result.total_time:
        return f"Local check: {result.message}"
    first_ms = result.first_token
    verdict = "acceptable for Mac Air" if first_ms <= 1.5 else "borderline – consider TinyLlama"
    return (
        f"Local check ({result.model}): first token {first_ms:.2f}s, total {result.total_time:.2f}s → {verdict}."
    )


def format_hosted(result: HostedResult) -> str:
    if result.message != "ok" or not result.latency:
        return f"Hosted check: {result.message}"
    return (
        f"Hosted check ({result.model} via Groq): latency {result.latency:.2f}s – free dev tier."  # type: ignore[str-format]
    )


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    health = fetch_health()
    health_model = None
    if health:
        if health.get("provider_type") == "local" and isinstance(health.get("model_name"), str):
            health_model = health["model_name"]
    candidate_models = []
    if health_model:
        candidate_models.append(health_model)
    candidate_models.extend(["phi3:mini", "tinyllama"])

    local_result = LocalResult(None, None, None, "Local provider not available")
    for candidate in candidate_models:
        local_result = measure_local(candidate, DEFAULT_OLLAMA)
        if local_result.message == "ok":
            break

    hosted_model: Optional[str] = None
    if isinstance(health, dict):
        candidate = health.get("hosted_model_name") or (
            health.get("model_name") if health.get("provider_type") == "hosted" else None
        )
        if isinstance(candidate, str):
            hosted_model = candidate
    if not hosted_model:
        hosted_model = os.getenv("HOSTED_MODEL_NAME")
    if not hosted_model:
        hosted_model = "llama-3.1-8b-instant"
    hosted_result = measure_hosted(hosted_model, os.getenv("GROQ_API_KEY"), os.getenv("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"))

    lines = [
        f"Mac Air benchmark – {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        format_local(local_result),
        format_hosted(hosted_result),
        "Recommendation: Stay local unless the response feels slow or the question is complex.",
    ]

    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
