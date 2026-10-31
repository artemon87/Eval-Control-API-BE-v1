import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from src.models import TimeWindow
from src.models.unit_history import (
    CaseState,
    HistoryBranch,
    HistoryCase,
    HistoryCaseCell,
    HistoryCohort,
    HistoryCommit,
    HistoryRun,
    HistorySignals,
    UnitHistoryInsights,
    UnitHistoryScope,
)
from src.repositories.unit_history import history_window


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _date(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()[:24]


def _verdict(values: list[Any]) -> CaseState:
    aliases = {
        "pass": "passed",
        "fail": "failed",
        "error": "blocked",
        "block": "blocked",
        "xpass": "xpassed",
    }
    normalized = {aliases.get(str(value).lower(), str(value).lower()) for value in values}
    for verdict in ("failed", "blocked", "xpassed", "pending", "skipped", "passed"):
        if verdict in normalized:
            return cast(CaseState, verdict)
    return "pending"


@dataclass
class _Case:
    key: str
    label: str
    test_type: str | None
    verdict: CaseState
    definition: str


@dataclass
class _Run:
    model: HistoryRun
    config: dict[str, Any]
    complete_config: bool
    cases: dict[str, _Case] = field(default_factory=dict)

    @property
    def definitions(self) -> dict[str, str]:
        return {key: case.definition for key, case in self.cases.items()}


def _configuration(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    metrics = raw.get("metrics")
    metrics = (
        sorted({_text(item) for item in metrics if _text(item)})
        if isinstance(metrics, list)
        else []
    )
    bsa = _text(raw.get("bsa_version"))
    mode = _text(raw.get("mode"))
    config = {
        "bsa_version": bsa,
        "bsa_environment": _text(raw.get("bsa_environment")),
        "mode": mode,
        "metrics": metrics,
        "evaluation_layer": _text(raw.get("evaluation_layer")),
        "agent_name": _text(raw.get("agent_name")),
        "category": _text(raw.get("category")),
        "n_runs": _number(raw.get("n_runs")),
        "pass_rate_threshold": _number(raw.get("pass_rate_threshold")),
    }
    return config, bool(bsa and bsa.lower() != "unknown" and mode and metrics)


def _prepare_runs(
    raw_runs: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    window: TimeWindow,
) -> list[_Run]:
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        by_run[str(observation.get("_id", {}).get("run_id"))].append(observation)
    result: list[_Run] = []
    seen_ids: set[str] = set()
    for raw in raw_runs:
        run_id = _text(raw.get("run_id"))
        at = _date(raw.get("started_at"))
        if not run_id or not at or run_id in seen_ids:
            continue
        if not cast(datetime, window.started_from) <= at < cast(datetime, window.started_before):
            continue
        seen_ids.add(run_id)
        summary_value = raw.get("summary")
        summary: dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
        config_value = raw.get("unit_config")
        config_raw: dict[str, Any] = config_value if isinstance(config_value, dict) else {}
        config, complete_config = _configuration(config_raw)
        branch = _text(raw.get("git_ref"))
        if branch and branch.startswith("refs/heads/"):
            branch = branch[len("refs/heads/") :]
        branch_key = _json(branch)
        total, passed = _integer(summary.get("total")), _integer(summary.get("passed"))
        if total is None or passed is None or passed > total:
            total, passed = None, None
        status = _text(raw.get("execution_status")) or "unknown"
        record_count = sum(_integer(item.get("records")) or 0 for item in by_run[run_id])
        model = HistoryRun(
            run_id=run_id,
            branch_key=branch_key,
            branch_label=branch or "Branch not recorded",
            git_sha=_text(raw.get("git_sha")),
            actor=_text(raw.get("actor")),
            trigger=_text(raw.get("trigger")),
            started_at=at,
            execution_status=status,
            verdict=_text(raw.get("verdict")) or "pending",
            skill_version=_text(config_raw.get("skill_version")),
            total=total,
            passed=passed,
            pass_rate_pct=passed / total * 100 if passed is not None and total else None,
            duration_ms=_number(raw.get("duration_ms")),
            case_count=0,
            cases_complete=False,
            cohort_key=None,
        )
        run = _Run(model=model, config=config, complete_config=complete_config)
        identity_complete = True
        for item in by_run[run_id]:
            identity = item.get("_id", {})
            name = _text(identity.get("name"))
            test_type = _text(identity.get("test_type"))
            if not name:
                identity_complete = False
                continue
            key = _digest([name, test_type])
            definitions = []
            for definition in item.get("definitions", []):
                if not isinstance(definition, dict):
                    continue
                metrics = definition.get("metrics", [])
                definitions.append(
                    _json(
                        {
                            "thresholds": definition.get("thresholds", {}),
                            "metrics": sorted(str(metric) for metric in metrics),
                        }
                    )
                )
            run.cases[key] = _Case(
                key=key,
                label=name,
                test_type=test_type,
                verdict=_verdict(item.get("verdicts", [])),
                definition=_json(sorted(set(definitions))),
            )
        model.case_count = len(run.cases)
        model.cases_complete = bool(
            status == "completed"
            and total
            and record_count == total
            and identity_complete
            and run.cases
        )
        if model.cases_complete:
            model.case_set_key = _digest(run.definitions)
            # A subset still supplies evidence for its executed cases. Cohorts match
            # runtime/evaluator settings and threshold profiles, not case-set size.
            model.cohort_key = _digest([config, sorted(set(run.definitions.values()))])
        result.append(run)
    # Keep the repository's _id ordering when executions have identical timestamps.
    return sorted(result, key=lambda run: run.model.started_at)


def _cohorts(runs: list[_Run]) -> tuple[list[HistoryCohort], dict[str, list[_Run]]]:
    groups: dict[str, list[_Run]] = defaultdict(list)
    for run in runs:
        if run.model.cohort_key:
            groups[run.model.cohort_key].append(run)
    ordered = sorted(
        groups,
        key=lambda key: (
            -len(groups[key]),
            -len(groups[key][0].cases),
            -groups[key][-1].model.started_at.timestamp(),
            key,
        ),
    )
    cohorts = []
    for ordinal, key in enumerate(ordered, start=1):
        group, example = groups[key], groups[key][0]
        label = f"Settings {ordinal} · BSA {example.config['bsa_version'] or 'not recorded'} · "
        case_count = len({case for run in group for case in run.cases})
        label += f"{example.config['mode'] or 'mode not recorded'} · {case_count} case identities"
        details = [
            f"Runtime: {example.config['bsa_environment'] or 'not recorded'} / "
            f"BSA {example.config['bsa_version'] or 'not recorded'}",
            "Metrics: " + (", ".join(example.config["metrics"]) or "not recorded"),
        ]
        for field_name, title in [
            ("evaluation_layer", "Evaluation layer"),
            ("agent_name", "Agent"),
            ("category", "Category"),
            ("n_runs", "Configured repetitions"),
            ("pass_rate_threshold", "Recorded pass-rate gate"),
        ]:
            value = example.config[field_name]
            if value is not None:
                details.append(f"{title}: {value}")
        thresholds: set[str] = set()
        for case in example.cases.values():
            for encoded in json.loads(case.definition):
                definition = json.loads(encoded)
                if isinstance(definition.get("thresholds"), dict):
                    thresholds.update(
                        f"{metric}={value}" for metric, value in definition["thresholds"].items()
                    )
        details.append("Case thresholds: " + (", ".join(sorted(thresholds)) or "not recorded"))
        cohorts.append(
            HistoryCohort(
                key=key,
                label=label,
                run_count=len(group),
                case_count=case_count,
                branch_count=len({run.model.branch_key for run in group}),
                configuration_complete=example.complete_config,
                details=details,
            )
        )
    return cohorts, groups


def _inconsistent_groups(runs: list[_Run]) -> list[tuple[list[_Run], set[str]]]:
    groups: dict[tuple[str, str, str], list[_Run]] = defaultdict(list)
    for run in runs:
        if (
            run.model.git_sha
            and run.complete_config
            and run.model.branch_label != "Branch not recorded"
        ):
            groups[(run.model.branch_key, run.model.git_sha, _digest(run.definitions))].append(run)
    inconsistent = []
    for group in groups.values():
        if len(group) < 2:
            continue
        cases = {
            key
            for key in group[0].cases
            if {run.cases[key].verdict for run in group} >= {"passed", "failed"}
        }
        if cases:
            inconsistent.append((group, cases))
    return inconsistent


def _commits(runs: list[_Run]) -> list[HistoryCommit]:
    groups: dict[str, list[_Run]] = {}
    for run in runs:
        key = "sha:" + run.model.git_sha if run.model.git_sha else "run:" + run.model.run_id
        groups.setdefault(key, []).append(run)
    result = []
    for key, group in groups.items():
        rates = [
            run.model.pass_rate_pct
            for run in group
            if run.model.execution_status == "completed" and run.model.pass_rate_pct is not None
        ]
        result.append(
            HistoryCommit(
                key=key,
                sha=group[0].model.git_sha,
                first_at=group[0].model.started_at,
                last_at=group[-1].model.started_at,
                run_ids=[run.model.run_id for run in group],
                low_pass_rate=min(rates) if rates else None,
                high_pass_rate=max(rates) if rates else None,
            )
        )
    return result


def _branches(
    runs: list[_Run], inconsistent: list[tuple[list[_Run], set[str]]]
) -> list[HistoryBranch]:
    groups: dict[str, list[_Run]] = defaultdict(list)
    for run in runs:
        groups[run.model.branch_key].append(run)
    result = []
    for key, group in groups.items():
        completed = [run for run in group if run.model.cases_complete]
        first = completed[0] if completed else None
        latest = group[-1]
        last = completed[-1] if completed else None
        direction = "insufficient"
        comparable = bool(
            first
            and last
            and first is not last
            and first.complete_config
            and last.complete_config
            and first.model.cohort_key == last.model.cohort_key
            and first.definitions == last.definitions
            and first.model.git_sha
            and last.model.git_sha
            and first.model.git_sha != last.model.git_sha
            and first.model.branch_label != "Branch not recorded"
            and latest is last
        )
        if comparable and first and last:
            change = cast(float, last.model.pass_rate_pct) - cast(float, first.model.pass_rate_pct)
            direction = "improved" if change > 0 else "declined" if change < 0 else "unchanged"
        elif (
            first
            and last
            and first is not last
            and (
                first.model.cohort_key != last.model.cohort_key
                or first.definitions != last.definitions
            )
        ):
            direction = "context_changed"
        previous = [
            run
            for run in completed
            if run is not latest
            and set(run.cases) > set(latest.cases)
            and latest.model.cases_complete
            and all(
                run.cases[case].definition == latest.cases[case].definition for case in latest.cases
            )
        ]
        expected = max((len(run.cases) for run in previous), default=None)
        result.append(
            HistoryBranch(
                key=key,
                label=latest.model.branch_label,
                run_count=len(group),
                latest_run_id=latest.model.run_id,
                latest_actor=latest.model.actor,
                latest_at=latest.model.started_at,
                direction=cast(Any, direction),
                first_pass_rate=first.model.pass_rate_pct if first else None,
                latest_pass_rate=last.model.pass_rate_pct if last else None,
                first_total=first.model.total if first else None,
                latest_total=last.model.total if last else None,
                latest_context_match=latest.model.context_match,
                coverage_expected=expected,
                coverage_actual=len(latest.cases) if latest.model.cases_complete else None,
                fewer_cases=expected is not None,
                inconsistent_latest=any(
                    item[0].model.branch_key == key
                    and item[0].model.git_sha == latest.model.git_sha
                    for item, _ in inconsistent
                ),
                commits=_commits(group),
            )
        )
    return sorted(result, key=lambda branch: (branch.label != "main", branch.label))


def _cases(
    runs: list[_Run],
    matching: list[_Run],
    branches: list[HistoryBranch],
    inconsistent: list[tuple[list[_Run], set[str]]],
) -> list[HistoryCase]:
    identities: dict[str, _Case] = {}
    for run in runs:
        identities.update(run.cases)
    by_branch = {
        branch.key: [run for run in runs if run.model.branch_key == branch.key]
        for branch in branches
    }
    matching_by_branch: dict[str, list[_Run]] = defaultdict(list)
    for run in matching:
        matching_by_branch[run.model.branch_key].append(run)
    result = []
    for key, identity in identities.items():
        cells = []
        failures = []
        for branch in branches:
            observed = [
                run for run in matching if run.model.branch_key == branch.key and key in run.cases
            ]
            failed = [run for run in observed if run.cases[key].verdict == "failed"]
            failures.extend(failed)
            latest = by_branch[branch.key][-1]
            state: CaseState = "unavailable"
            if latest.model.cases_complete:
                state = latest.cases[key].verdict if key in latest.cases else "not_run"
            cells.append(
                HistoryCaseCell(
                    branch_key=branch.key,
                    executions=len(observed),
                    failed=len(failed),
                    passed=sum(run.cases[key].verdict == "passed" for run in observed),
                    blocked=sum(run.cases[key].verdict == "blocked" for run in observed),
                    xpassed=sum(run.cases[key].verdict == "xpassed" for run in observed),
                    skipped=sum(run.cases[key].verdict == "skipped" for run in observed),
                    pending=sum(run.cases[key].verdict == "pending" for run in observed),
                    latest_state=state,
                    latest_run_id=latest.model.run_id,
                    latest_context_match=latest.model.context_match,
                    latest_at=latest.model.started_at,
                )
            )
        result.append(
            HistoryCase(
                key=key,
                label=identity.label,
                test_type=identity.test_type,
                executions=sum(cell.executions for cell in cells),
                failed=len(failures),
                affected_branches=sum(cell.failed > 0 for cell in cells),
                first_failed_at=min((run.model.started_at for run in failures), default=None),
                last_failed_at=max((run.model.started_at for run in failures), default=None),
                inconsistent_updates=len(
                    {
                        (group[0].model.branch_key, group[0].model.git_sha)
                        for group, keys in inconsistent
                        if key in keys
                    }
                ),
                cells=cells,
            )
        )
    return sorted(result, key=lambda case: (-case.failed, case.label, case.test_type or ""))


def build_history_insights(
    scope: UnitHistoryScope,
    time_window: TimeWindow,
    raw_runs: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    cohort_key: str | None = None,
) -> UnitHistoryInsights:
    window = history_window(time_window)
    runs = _prepare_runs(raw_runs, observations, window)
    scope_complete = bool(
        scope.github_repository
        and scope.target_environment
        and scope.stage != "unknown"
        and scope.environment != "unknown"
    )
    for run in runs:
        run.complete_config = run.complete_config and scope_complete
    cohorts, groups = _cohorts(runs)
    selected = cohort_key or (cohorts[0].key if cohorts else None)
    if cohort_key and cohort_key not in groups:
        raise ValueError("History analysis configuration is not present in this window")
    matching = groups.get(selected, []) if selected else []
    for run in runs:
        run.model.context_match = bool(selected and run.model.cohort_key == selected)
    inconsistent = _inconsistent_groups(matching)
    branches = _branches(runs, inconsistent)
    cases = _cases(runs, matching, branches, inconsistent)
    recurring = next((case for case in cases if case.failed), None)
    signals = HistorySignals(
        recurring_case_key=recurring.key if recurring else None,
        inconsistent_updates=len(
            {(group[0].model.branch_key, group[0].model.git_sha) for group, _ in inconsistent}
        ),
        inconsistent_case_keys=sorted({key for _, keys in inconsistent for key in keys}),
        coverage_gap_branch_keys=[branch.key for branch in branches if branch.fewer_cases],
        excluded_runs=sum(run.model.cases_complete and not run.model.context_match for run in runs),
        incomplete_case_runs=sum(
            run.model.execution_status == "completed" and not run.model.cases_complete
            for run in runs
        ),
        operational_runs=sum(run.model.execution_status in {"blocked", "error"} for run in runs),
    )
    return UnitHistoryInsights(
        scope=scope,
        started_from=cast(datetime, window.started_from),
        started_before=cast(datetime, window.started_before),
        cohorts=cohorts,
        selected_cohort_key=selected,
        runs=[run.model for run in reversed(runs)],
        branches=branches,
        cases=cases,
        signals=signals,
    )
