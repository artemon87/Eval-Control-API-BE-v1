from collections import defaultdict
from statistics import fmean
from typing import Any

from app.models import MetricComparison, UnitComparison, UnitRun
from app.repositories import UnitRepository


def _metric_means(cases: list[dict[str, Any]], run_id: str) -> dict[str, float]:
    values: dict[str, list[float]] = defaultdict(list)
    for case in cases:
        if case.get("run_id") != run_id:
            continue
        for metric, score in (case.get("scores") or {}).items():
            if isinstance(score, int | float):
                values[metric.lower()].append(float(score))
    return {metric: round(fmean(scores), 3) for metric, scores in values.items()}


async def build_unit_comparison(
    repository: UnitRepository,
    *,
    skill: str,
    environment: str | None,
    baseline_version: str | None,
    candidate_version: str | None,
) -> UnitComparison:
    version_runs = await repository.latest_runs_by_version(skill, environment)
    if not version_runs:
        raise LookupError(f"No completed unit runs found for skill '{skill}'")

    versions = [str(run["unit_config"]["skill_version"]) for run in version_runs]
    candidate = candidate_version or versions[0]
    if baseline_version:
        baseline: str | None = baseline_version
    else:
        baseline = next((version for version in versions if version != candidate), None)
        if baseline is None:
            raise LookupError("At least two distinct skill versions are required for comparison")

    if baseline == candidate:
        raise ValueError("baseline_version and candidate_version must be different")
    assert baseline is not None

    candidate_document = await repository.latest_run_for_version(skill, candidate, environment)
    baseline_document = await repository.latest_run_for_version(skill, baseline, environment)
    if not candidate_document or not baseline_document:
        raise LookupError("A completed run was not found for one of the requested versions")

    candidate_run = UnitRun.model_validate(candidate_document)
    baseline_run = UnitRun.model_validate(baseline_document)
    cases = await repository.cases_for_runs([baseline_run.run_id, candidate_run.run_id])
    baseline_metrics = _metric_means(cases, baseline_run.run_id)
    candidate_metrics = _metric_means(cases, candidate_run.run_id)
    metric_names = sorted(baseline_metrics.keys() | candidate_metrics.keys())
    metrics = [
        MetricComparison(
            metric=name,
            baseline=baseline_metrics.get(name),
            candidate=candidate_metrics.get(name),
            delta=(
                round(candidate_metrics[name] - baseline_metrics[name], 3)
                if name in baseline_metrics and name in candidate_metrics
                else None
            ),
        )
        for name in metric_names
    ]

    duration_delta = None
    if baseline_run.duration_ms is not None and candidate_run.duration_ms is not None:
        duration_delta = round(candidate_run.duration_ms - baseline_run.duration_ms, 3)

    return UnitComparison(
        skill=skill,
        environment=environment,
        baseline_version=baseline,
        candidate_version=candidate,
        baseline_run=baseline_run,
        candidate_run=candidate_run,
        pass_rate_delta=round(
            candidate_run.summary.pass_rate_pct - baseline_run.summary.pass_rate_pct, 3
        ),
        duration_delta_ms=duration_delta,
        metrics=metrics,
    )
