"""Helpers for runtime latency profiling and compact health summaries."""

from __future__ import annotations

import math
from collections import Counter, deque
from typing import Iterable

from kindshot.models import PipelineLatencyProfile

_STAGE_FIELDS = (
    "news_to_pipeline_ms",
    "context_card_ms",
    "decision_total_ms",
    "guardrail_ms",
    "order_attempt_ms",
    "pipeline_total_ms",
    "llm_latency_ms",
)


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def summarize_latency_samples(values: Iterable[int]) -> dict[str, int]:
    samples = [int(value) for value in values if value is not None]
    if not samples:
        return {"samples": 0}
    total = sum(samples)
    return {
        "samples": len(samples),
        "avg_ms": int(round(total / len(samples))),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def identify_bottleneck_stage(profile: PipelineLatencyProfile) -> str | None:
    candidates = {
        "context_card": profile.context_card_ms,
        "decision": profile.decision_total_ms,
        "guardrail": profile.guardrail_ms,
        "order_attempt": profile.order_attempt_ms,
    }
    available = {stage: int(value) for stage, value in candidates.items() if value is not None}
    if not available:
        return None
    return max(available.items(), key=lambda item: item[1])[0]


class RecentLatencyTracker:
    def __init__(self, window_size: int = 200) -> None:
        resolved_size = max(1, int(window_size))
        self._window_size = resolved_size
        self._samples = {
            field: deque(maxlen=resolved_size)
            for field in _STAGE_FIELDS
        }
        self._bottlenecks: Counter[str] = Counter()
        self._decision_sources: Counter[str] = Counter()
        self._cache_layers: Counter[str] = Counter()

    def record(
        self,
        profile: PipelineLatencyProfile,
        *,
        decision_source: str | None = None,
    ) -> None:
        for field in _STAGE_FIELDS:
            value = getattr(profile, field)
            if value is not None:
                self._samples[field].append(int(value))
        if profile.bottleneck_stage:
            self._bottlenecks[profile.bottleneck_stage] += 1
        if decision_source:
            self._decision_sources[decision_source] += 1
        if profile.llm_cache_layer:
            self._cache_layers[profile.llm_cache_layer] += 1

    def snapshot(self) -> dict[str, object]:
        stages = {
            field: summarize_latency_samples(values)
            for field, values in self._samples.items()
        }
        return {
            "window_size": self._window_size,
            "stages": stages,
            "bottlenecks": dict(self._bottlenecks.most_common(5)),
            "decision_sources": dict(self._decision_sources),
            "cache_layers": dict(self._cache_layers),
        }
