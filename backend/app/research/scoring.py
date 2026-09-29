from __future__ import annotations

import math

from app.db.models import Candidate
from app.schemas.research import MetricRule


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def score_candidates(
    candidates: list[Candidate],
    rules: list[MetricRule],
    *,
    config_version: str,
    minimum_total_score: float,
) -> list[Candidate]:
    ranges: dict[str, tuple[float, float]] = {}
    for rule in rules:
        values = [
            value
            for candidate in candidates
            if (value := _numeric((candidate.raw_metrics_json or {}).get(rule.name))) is not None
        ]
        if values:
            ranges[rule.name] = (min(values), max(values))

    for candidate in candidates:
        normalized: dict[str, float | None] = {}
        missing_required: list[str] = []
        weighted_sum = 0.0
        available_weight = 0.0
        raw_metrics = candidate.raw_metrics_json or {}
        for rule in rules:
            raw_value = _numeric(raw_metrics.get(rule.name))
            if raw_value is None:
                normalized[rule.name] = None
                if rule.missing_policy == "reject":
                    missing_required.append(rule.name)
                elif rule.missing_policy == "zero":
                    available_weight += rule.weight
                continue
            minimum, maximum = ranges[rule.name]
            value = 1.0 if maximum == minimum else (raw_value - minimum) / (maximum - minimum)
            if rule.direction == "lower":
                value = 1.0 - value
            normalized[rule.name] = value
            weighted_sum += value * rule.weight
            available_weight += rule.weight

        candidate.normalized_metrics_json = normalized
        candidate.score_config_version = config_version
        candidate.rank = None
        if missing_required:
            candidate.total_score = None
            candidate.selection_status = "rejected"
            candidate.selection_reason = "缺少必需指标：" + "、".join(missing_required)
            continue
        if available_weight <= 0:
            candidate.total_score = None
            candidate.selection_status = "rejected"
            candidate.selection_reason = "没有可参与评分的指标"
            continue
        candidate.total_score = weighted_sum / available_weight
        if candidate.total_score >= minimum_total_score:
            candidate.selection_status = "qualified"
            candidate.selection_reason = f"通过评分配置 {config_version} 的最低阈值"
        else:
            candidate.selection_status = "rejected"
            candidate.selection_reason = f"低于评分配置 {config_version} 的最低阈值"

    rankable = [candidate for candidate in candidates if candidate.total_score is not None]
    rankable.sort(key=lambda item: (-float(item.total_score or 0.0), item.created_at, str(item.id)))
    for rank, candidate in enumerate(rankable, start=1):
        candidate.rank = rank
    return sorted(
        candidates,
        key=lambda item: (
            item.rank is None,
            item.rank if item.rank is not None else 10**9,
            item.created_at,
        ),
    )
