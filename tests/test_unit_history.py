from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models import TimeWindow
from src.repositories.unit_history import (
    decode_scope,
    history_run_query,
    history_window,
    list_history_scopes,
    load_history_inputs,
    make_scope,
)
from src.services.unit_history import build_history_insights

WINDOW = TimeWindow(
    started_from=datetime(2026, 10, 1, tzinfo=UTC),
    started_before=datetime(2026, 10, 31, tzinfo=UTC),
)
SCOPE = make_scope(
    {
        "skill": "payroll",
        "github_repository": "org/skills",
        "stage": "staging",
        "target_environment": "east",
        "environment": "runner",
    }
)


def execution(
    name: str,
    day: int,
    branch: str | None,
    sha: str | None,
    passed: list[int],
    *,
    covered: list[int] | None = None,
    bsa: str = "3.14",
    actor: str = "sam",
    status: str = "completed",
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    covered = list(range(7)) if covered is None else covered
    raw = {
        "run_id": name,
        "started_at": datetime(2026, 10, day, tzinfo=UTC),
        "git_ref": branch,
        "git_sha": sha,
        "actor": actor,
        "trigger": "ci",
        "execution_status": status,
        "verdict": "passed" if len(passed) == len(covered) else "failed",
        "environment": "runner",
        "duration_ms": 1200,
        "summary": {"total": len(covered), "passed": len(passed)},
        "unit_config": {
            "bsa_version": bsa,
            "bsa_environment": "runner",
            "mode": "all",
            "metrics": ["accuracy"],
            "skill_version": "0.8.0",
            **(config or {}),
        },
    }
    cases = [
        {
            "_id": {"run_id": name, "name": f"case-{i}", "test_type": "single-turn"},
            "verdicts": ["passed" if i in passed else "failed"],
            "definitions": [{"metrics": ["accuracy"], "thresholds": {"accuracy": 4}}],
            "records": 1,
        }
        for i in covered
    ]
    return raw, cases


def sample() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [
        execution("m1", 16, "refs/heads/main", "a31", [0, 1, 2, 3, 6]),
        execution("m2", 28, "main", "b40", [0, 1, 2, 3, 4, 6]),
        execution("p1", 18, "feature/policy", "c21", [0, 1, 2, 6]),
        execution("p2", 24, "feature/policy", "d50", [0, 1, 2, 3, 4, 6]),
        execution("p3", 30, "feature/policy", "e70", [0, 1, 2, 3, 4, 5]),
        execution("p4", 30, "feature/policy", "e70", list(range(7)), actor="ci-bot"),
        execution("p5", 30, "feature/policy", "e70", [0, 1, 2, 3, 4, 5]),
        execution("f1", 20, "feature/format", "f11", [0, 1, 2, 3, 6]),
        execution("f2", 29, "feature/format", "f67", list(range(7))),
        execution("f3", 29, "feature/format", "f67", list(range(7))),
        execution("t1", 22, "experiment/timeout", "a91", [0, 1, 2, 3, 6]),
        execution(
            "t2",
            30,
            "experiment/timeout",
            "b24",
            [0, 1, 4, 5],
            covered=[0, 1, 4, 5],
            bsa="3.15",
        ),
    ]
    return [row[0] for row in rows], [case for _, cases in rows for case in cases]


def test_global_evidence_counts_match_the_approved_landscape() -> None:
    runs, cases = sample()
    original = deepcopy((runs, cases))
    result = build_history_insights(SCOPE, WINDOW, runs, cases)
    assert len(result.runs) == 12
    assert len(result.branches) == 4
    cohort = next(item for item in result.cohorts if item.key == result.selected_cohort_key)
    assert cohort.run_count == 11
    recurring = next(item for item in result.cases if item.key == result.signals.recurring_case_key)
    assert recurring.label == "case-5"
    assert (recurring.failed, recurring.executions, recurring.affected_branches) == (
        6,
        11,
        4,
    )
    assert result.signals.inconsistent_updates == 1
    inconsistent = next(
        item for item in result.cases if item.key in result.signals.inconsistent_case_keys
    )
    assert inconsistent.label == "case-6"
    assert inconsistent.inconsistent_updates == 1
    branches = {branch.label: branch for branch in result.branches}
    assert branches["feature/policy"].direction == "improved"
    assert branches["feature/policy"].inconsistent_latest
    assert len(branches["feature/policy"].commits[-1].run_ids) == 3
    assert branches["feature/format"].direction == "improved"
    assert branches["experiment/timeout"].direction == "context_changed"
    assert branches["experiment/timeout"].fewer_cases
    assert branches["experiment/timeout"].coverage_actual == 4
    assert branches["experiment/timeout"].coverage_expected == 7
    assert result.signals.excluded_runs == 1
    assert (runs, cases) == original
    encoded = result.model_dump(mode="json", by_alias=True)
    assert encoded["signals"]["inconsistentUpdates"] == 1
    assert encoded["scope"]["targetEnvironment"] == "east"


def test_latest_cells_include_all_branches_and_distinguish_not_run_from_unavailable() -> None:
    runs, cases = sample()
    partial, partial_cases = execution("p6", 30, "feature/policy", "new", [0, 1, 2])
    runs.append(partial)
    cases.extend(partial_cases[:2])
    result = build_history_insights(SCOPE, WINDOW, runs, cases)
    item = next(case for case in result.cases if case.label == "case-6")
    branches = {branch.label: branch.key for branch in result.branches}
    cells = {cell.branch_key: cell for cell in item.cells}
    assert cells[branches["experiment/timeout"]].latest_state == "not_run"
    assert not cells[branches["experiment/timeout"]].latest_context_match
    assert cells[branches["feature/policy"]].latest_state == "unavailable"
    assert cells[branches["main"]].latest_state == "passed"
    assert result.signals.incomplete_case_runs == 1
    assert len(item.cells) == 4


def test_missing_shas_and_branches_never_create_fake_repeatability_or_progress() -> None:
    entries = [execution(f"run-{i}", 20 + i, None, None, list(range(6 + i))) for i in range(2)]
    result = build_history_insights(
        SCOPE, WINDOW, [r for r, _ in entries], [c for _, cs in entries for c in cs]
    )
    assert result.signals.inconsistent_updates == 0
    assert result.branches[0].label == "Branch not recorded"
    assert result.branches[0].direction == "insufficient"
    assert len(result.branches[0].commits) == 2
    assert result.branches[0].commits[0].sha is None


def test_threshold_changes_same_sha_form_separate_cohorts_and_do_not_count_as_reruns() -> None:
    a, ac = execution("a", 20, "main", "same", list(range(6)))
    b, bc = execution("b", 21, "main", "same", list(range(7)))
    bc[0]["definitions"][0]["thresholds"]["accuracy"] = 3
    result = build_history_insights(SCOPE, WINDOW, [a, b], ac + bc)
    assert len(result.cohorts) == 2
    assert result.signals.inconsistent_updates == 0
    assert result.branches[0].direction == "context_changed"
    other = next(
        cohort.key for cohort in result.cohorts if cohort.key != result.selected_cohort_key
    )
    selected = build_history_insights(SCOPE, WINDOW, [a, b], ac + bc, other)
    assert selected.selected_cohort_key == other
    with pytest.raises(ValueError, match="not present"):
        build_history_insights(SCOPE, WINDOW, [a, b], ac + bc, "stale-cohort")


def test_missing_runtime_metadata_prevents_conclusive_direction_and_repeat_signals() -> None:
    a, ac = execution("a", 20, "main", "first", list(range(6)), bsa="unknown")
    b, bc = execution("b", 21, "main", "second", list(range(7)), bsa="unknown")
    result = build_history_insights(SCOPE, WINDOW, [a, b], ac + bc)
    assert not result.cohorts[0].configuration_complete
    assert result.branches[0].direction == "insufficient"
    assert result.signals.inconsistent_updates == 0


def test_blocked_skipped_pending_and_xpass_are_not_counted_as_test_failures() -> None:
    raw, cases = execution("a", 20, "main", "first", list(range(7)))
    for case, verdict in zip(cases[:4], ["error", "skipped", "pending", "xpassed"], strict=True):
        case["verdicts"] = [verdict]
    error, _ = execution("infra", 21, "main", "second", [], status="error")
    result = build_history_insights(SCOPE, WINDOW, [raw, error], cases)
    assert result.signals.recurring_case_key is None
    assert result.signals.operational_runs == 2
    assert len(result.runs) == 2
    assert all(case.failed == 0 for case in result.cases)
    assert all(cell.latest_state == "blocked" for case in result.cases for cell in case.cells)
    assert sum(cell.blocked for case in result.cases for cell in case.cells) == 1
    assert sum(cell.xpassed for case in result.cases for cell in case.cells) == 1


def test_null_summary_and_case_records_do_not_crash_or_create_zero_score_improvements() -> None:
    raw, cases = execution("a", 20, "main", "first", list(range(7)))
    raw["summary"] = {"total": None, "passed": None, "mean_score": None}
    result = build_history_insights(SCOPE, WINDOW, [raw], cases)
    assert result.runs[0].pass_rate_pct is None
    assert not result.runs[0].cases_complete
    assert result.selected_cohort_key is None
    assert result.signals.incomplete_case_runs == 1


def test_full_half_open_window_has_no_200_run_cap_and_ignores_future_injected_records() -> None:
    entries = [execution(f"r-{i:04}", 20, "main", f"sha-{i}", [0], covered=[0]) for i in range(240)]
    raw_runs, observations = (
        [r for r, _ in entries],
        [c for _, cs in entries for c in cs],
    )
    at_start = deepcopy(raw_runs[0])
    at_start.update(run_id="start", started_at=WINDOW.started_from)
    at_end = deepcopy(raw_runs[0])
    at_end.update(run_id="end", started_at=WINDOW.started_before)
    result = build_history_insights(SCOPE, WINDOW, raw_runs + [at_start, at_end], observations)
    assert len(result.runs) == 241
    assert "start" in {run.run_id for run in result.runs}
    assert "end" not in {run.run_id for run in result.runs}
    assert result.cohorts[0].run_count == 240


def test_scope_keys_preserve_repository_and_missing_metadata_without_broadening() -> None:
    assert decode_scope(SCOPE.scope_key) == SCOPE
    missing = make_scope(
        {
            "skill": "payroll",
            "github_repository": None,
            "stage": "runner",
            "target_environment": None,
            "environment": "runner",
        }
    )
    assert decode_scope(missing.scope_key).target_environment is None
    assert missing.scope_key != SCOPE.scope_key
    query = history_run_query(missing, WINDOW)
    assert query["eval_type"] == "unit" and query["skill"] == "payroll"
    assert query["started_at"] == {
        "$gte": WINDOW.started_from,
        "$lt": WINDOW.started_before,
    }
    literals = [part["$eq"][1]["$literal"] for part in query["$expr"]["$and"]]
    assert literals == [None, "runner", None, "runner"]
    assert "$trim" in str(query) and "unit_config.stage" in str(query)
    for invalid in ("", "not-a-scope!", "W10", "bnVsbA", "e30"):
        with pytest.raises(ValueError, match="Invalid history scope"):
            decode_scope(invalid)


def test_window_requires_both_bounds_and_rejects_unbounded_or_excessive_queries() -> None:
    assert history_window(WINDOW) == WINDOW
    with pytest.raises(ValueError, match="requires"):
        history_window(TimeWindow())
    with pytest.raises(ValueError, match="positive"):
        history_window(
            TimeWindow(started_from=WINDOW.started_before, started_before=WINDOW.started_from)
        )
    with pytest.raises(ValueError, match="90 days"):
        history_window(
            TimeWindow(
                started_from=WINDOW.started_from,
                started_before=WINDOW.started_from + timedelta(days=91),
            )
        )


@pytest.mark.asyncio
async def test_catalog_exhausts_scope_results_without_time_or_completion_limits() -> None:
    cursor = AsyncMock()
    cursor.to_list.return_value = [
        {
            "skill": "payroll",
            "github_repository": None,
            "stage": "staging",
            "target_environment": None,
            "environment": "runner",
        }
    ]
    collection = AsyncMock()
    collection.aggregate.return_value = cursor
    result = await list_history_scopes(collection)
    assert result[0].target_environment is None
    cursor.to_list.assert_awaited_once_with(length=None)
    pipeline = collection.aggregate.call_args.args[0]
    assert not any("$limit" in part for part in pipeline)
    assert "execution_status" not in pipeline[0]["$match"]
    assert "started_at" not in pipeline[0]["$match"]


@pytest.mark.asyncio
async def test_repository_batches_case_aggregation_without_truncating_or_loading_traces() -> None:
    rows = [execution(f"r-{i}", 20, "main", "sha", [], covered=[])[0] for i in range(1001)]
    rows[-1]["execution_status"] = "error"
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=rows)
    runs = MagicMock()
    runs.find.return_value = cursor
    case_cursor = AsyncMock()
    case_cursor.to_list.return_value = []
    cases = AsyncMock()
    cases.aggregate.return_value = case_cursor
    result, observations = await load_history_inputs(runs, cases, SCOPE, WINDOW)
    assert len(result) == 1001 and not observations
    assert cases.aggregate.await_count == 3
    pipelines = [call.args[0] for call in cases.aggregate.call_args_list]
    assert [len(p[0]["$match"]["run_id"]["$in"]) for p in pipelines] == [500, 500, 1]
    assert "prompt" not in str(pipelines) and "tool_calls" not in str(pipelines)
    assert runs.find.call_args.args[1]["unit_config"] == 1
    cursor.to_list.assert_awaited_once_with(length=None)


def test_missing_target_or_repository_disables_conclusive_progress_signals() -> None:
    rows = [
        execution("a", 20, "main", "one", list(range(6))),
        execution("b", 21, "main", "two", list(range(7))),
    ]
    scope = make_scope(
        {
            **SCOPE.model_dump(exclude={"scope_key"}),
            "target_environment": None,
            "github_repository": None,
        }
    )
    result = build_history_insights(
        scope, WINDOW, [r for r, _ in rows], [c for _, cases in rows for c in cases]
    )
    assert not result.cohorts[0].configuration_complete
    assert result.branches[0].direction == "insufficient"


def test_same_timestamp_preserves_repository_order_instead_of_sorting_random_run_ids() -> None:
    first, fc = execution("zzz-earlier", 20, "main", "one", list(range(6)))
    last, lc = execution("aaa-later", 20, "main", "two", list(range(7)))
    result = build_history_insights(SCOPE, WINDOW, [first, last], fc + lc)
    assert result.runs[0].run_id == "aaa-later"
    assert result.branches[0].latest_run_id == "aaa-later"
    assert result.branches[0].direction == "improved"


def test_repeated_case_records_count_one_execution_and_preserve_a_real_failed_verdict() -> None:
    raw, cases = execution("a", 20, "main", "one", list(range(7)))
    raw["summary"] = {"total": 8, "passed": 7}
    cases[0]["records"] = 2
    cases[0]["verdicts"] = ["passed", "failed"]
    result = build_history_insights(SCOPE, WINDOW, [raw], cases)
    case = next(item for item in result.cases if item.label == "case-0")
    assert result.runs[0].cases_complete
    assert result.runs[0].case_count == 7
    assert case.executions == 1 and case.failed == 1
    assert not result.signals.coverage_gap_branch_keys


def test_intentional_subsets_contribute_only_executed_cases_without_claiming_improvement() -> None:
    first, fc = execution("a", 20, "main", "one", [0, 1, 2, 3, 6])
    last, lc = execution("b", 21, "main", "two", [0, 1, 4, 5], covered=[0, 1, 4, 5])
    result = build_history_insights(SCOPE, WINDOW, [first, last], fc + lc)
    assert len(result.cohorts) == 1
    assert result.cohorts[0].run_count == 2
    assert result.cohorts[0].case_count == 7
    assert result.signals.excluded_runs == 0
    assert result.branches[0].direction == "context_changed"
    assert result.branches[0].fewer_cases
    tested = next(item for item in result.cases if item.label == "case-4")
    not_tested = next(item for item in result.cases if item.label == "case-6")
    assert tested.executions == 2 and tested.failed == 1
    assert not_tested.executions == 1 and not_tested.cells[0].latest_state == "not_run"


def test_different_subsets_at_the_same_sha_do_not_create_a_repeat_disagreement() -> None:
    first, fc = execution("a", 20, "main", "same", [0, 1, 2, 3, 6])
    last, lc = execution("b", 21, "main", "same", [0, 1, 4, 5], covered=[0, 1, 4, 5])
    result = build_history_insights(SCOPE, WINDOW, [first, last], fc + lc)
    assert result.signals.inconsistent_updates == 0


def test_trend_case_set_identity_changes_even_when_case_count_and_cohort_match() -> None:
    first, fc = execution("a", 20, "main", "one", list(range(7)))
    same, sc = execution("b", 21, "main", "two", list(range(7)))
    changed, cc = execution("c", 22, "main", "three", list(range(7)))
    cc[0]["_id"]["name"] = "replacement-case"
    incomplete, ic = execution("d", 23, "main", "four", list(range(7)))
    result = build_history_insights(
        SCOPE,
        WINDOW,
        [first, same, changed, incomplete],
        fc + list(reversed(sc)) + cc + ic[:3],
    )
    by_id = {run.run_id: run for run in result.runs}
    assert by_id["a"].case_set_key == by_id["b"].case_set_key
    assert by_id["a"].case_set_key != by_id["c"].case_set_key
    assert by_id["a"].cohort_key == by_id["c"].cohort_key
    assert by_id["a"].case_count == by_id["c"].case_count
    assert by_id["d"].case_set_key is None
    assert "caseSetKey" in by_id["a"].model_dump(by_alias=True)


def test_partial_blocked_execution_never_becomes_a_quality_regression_or_improvement() -> None:
    good, gc = execution("good", 20, "feature/work", "first", list(range(6)))
    partial, pc = execution("partial", 21, "feature/work", "second", [])
    pc[0]["verdicts"] = ["error"]
    pc[0]["error"] = "HTTP 401: execution access rejected"
    partial["unit_config"]["pass_rate_threshold"] = 0.9
    good["unit_config"]["pass_rate_threshold"] = 0.9
    result = build_history_insights(SCOPE, WINDOW, [good, partial], gc + pc)
    by_id = {run.run_id: run for run in result.runs}
    assert by_id["good"].quality_measured
    assert not by_id["partial"].quality_measured
    assert by_id["partial"].blocked_cases == 1
    assert by_id["partial"].pass_rate_threshold == 90
    assert result.branches[0].direction == "insufficient"
    assert result.branches[0].latest_pass_rate == pytest.approx(600 / 7)
    assert result.execution_issues[0].case_name == "case-0"
    assert result.execution_issues[0].error == "HTTP 401: execution access rejected"
    assert result.execution_issues[0].run_id == "partial"
    assert (
        sum(case.failed for case in result.cases) == 7
    )  # six real partial failures, one earlier failure


def test_failed_case_verdict_is_measured_but_missing_nonfinal_or_inconsistent_evidence_is_not() -> (
    None
):
    raw, cases = execution("a", 20, "feature/work", "sha", list(range(6)))
    assert build_history_insights(SCOPE, WINDOW, [raw], cases).runs[0].quality_measured
    assert not build_history_insights(SCOPE, WINDOW, [raw], cases[:3]).runs[0].quality_measured
    raw["summary"]["passed"] = 7
    assert not build_history_insights(SCOPE, WINDOW, [raw], cases).runs[0].quality_measured
    for state in ("skipped", "pending", "xpassed"):
        changed = deepcopy(cases)
        changed[0]["verdicts"] = [state]
        assert not build_history_insights(SCOPE, WINDOW, [raw], changed).runs[0].quality_measured


def test_run_level_error_is_retained_without_inventing_a_root_cause_or_zero_quality() -> None:
    raw, _ = execution("infra", 20, "feature/work", "sha", [], status="error")
    raw["error"] = "HTTP 401"
    result = build_history_insights(SCOPE, WINDOW, [raw], [])
    assert not result.runs[0].quality_measured
    assert result.execution_issues[0].error == "HTTP 401"
    assert result.execution_issues[0].case_name is None
    assert result.execution_issues[0].model_dump(by_alias=True)["runId"] == "infra"


def test_broad_scope_keeps_repository_and_skill_filters() -> None:
    broad = SCOPE.model_copy(update={"all_stages": True, "all_targets": True})
    clauses = history_run_query(broad, WINDOW)["$expr"]["$and"]
    assert len(clauses) == 1  # exact repository remains; only stage/target/runtime broaden
    assert clauses[0]["$eq"][1] == {"$literal": "org/skills"}
    assert history_run_query(broad, WINDOW)["skill"] == "payroll"
    assert len(history_run_query(SCOPE, WINDOW)["$expr"]["$and"]) == 4


def test_all_stage_series_and_configurations_keep_latest_run_visible() -> None:
    rows = [
        execution(
            "old",
            10,
            "feature/test",
            "a",
            [0],
            covered=[0],
            bsa="1.17",
            config={"stage": "dev", "target_environment": "east"},
        ),
        execution(
            "new",
            30,
            "feature/test",
            "b",
            [0],
            covered=[0],
            bsa="1.18",
            config={"stage": "preprod", "target_environment": "west"},
        ),
    ]
    broad = SCOPE.model_copy(update={"all_stages": True, "all_targets": True})
    result = build_history_insights(
        broad, WINDOW, [r for r, _ in rows], [c for _, cases in rows for c in cases]
    )
    assert [run.run_id for run in result.runs] == ["new", "old"]
    assert len(result.branches) == 2
    assert len(result.cohorts) == 2
    assert result.runs[0].bsa_version == "1.18"
    assert result.runs[0].stage == "preprod"
    assert all(branch.direction == "insufficient" for branch in result.branches)


def test_adjacent_trend_deltas_and_configuration_labels() -> None:
    rows = [
        execution(
            "one", 10, "feature/a", "a", [0], covered=[0, 1], config={"agent_name": "agent-a"}
        ),
        execution(
            "two", 11, "feature/a", "b", [0, 1], covered=[0, 1], config={"agent_name": "agent-a"}
        ),
        execution(
            "three", 12, "feature/a", "c", [0, 1], covered=[0, 1], config={"agent_name": "agent-a"}
        ),
        execution(
            "new-bsa",
            13,
            "feature/a",
            "d",
            [0],
            covered=[0, 1],
            bsa="1.18",
            config={"agent_name": "agent-a"},
        ),
        execution(
            "other-branch",
            14,
            "feature/b",
            "e",
            [0],
            covered=[0, 1],
            config={"agent_name": "agent-a"},
        ),
    ]
    result = build_history_insights(
        SCOPE, WINDOW, [r for r, _ in rows], [c for _, cases in rows for c in cases]
    )
    runs = {run.run_id: run for run in result.runs}
    assert runs["two"].trend_previous_run_id == "one"
    assert runs["two"].trend_pass_rate_delta == 50
    assert runs["three"].trend_pass_rate_delta == 0
    assert runs["new-bsa"].trend_transition == "settings_changed"
    assert runs["new-bsa"].trend_pass_rate_delta is None
    assert runs["other-branch"].trend_previous_run_id is None
    assert all(
        "BSA " in cohort.label and "Skill 0.8.0" in cohort.label and "Agent agent-a" in cohort.label
        for cohort in result.cohorts
    )


def test_skill_and_agent_versions_split_settings_and_errors_break_trend() -> None:
    rows = [
        execution("base", 10, "feature/a", "a", [0], covered=[0], config={"agent_name": "agent-a"}),
        execution(
            "skill-change",
            11,
            "feature/a",
            "b",
            [0],
            covered=[0],
            config={"skill_version": "0.9.0", "agent_name": "agent-a"},
        ),
        execution(
            "agent-change",
            12,
            "feature/a",
            "c",
            [0],
            covered=[0],
            config={"skill_version": "0.9.0", "agent_name": "agent-b"},
        ),
        execution("error", 13, "feature/a", "d", [], covered=[], status="error"),
        execution("after-error", 14, "feature/a", "e", [0], covered=[0]),
    ]
    result = build_history_insights(
        SCOPE, WINDOW, [r for r, _ in rows], [c for _, cases in rows for c in cases]
    )
    runs = {run.run_id: run for run in result.runs}
    assert runs["skill-change"].trend_transition == "settings_changed"
    assert runs["agent-change"].trend_transition == "settings_changed"
    assert runs["after-error"].trend_previous_run_id == "error"
    assert runs["after-error"].trend_transition == "incomplete"
    assert runs["after-error"].trend_pass_rate_delta is None
    assert len(result.cohorts) >= 3


def test_same_timestamp_uses_repository_tie_order_for_trend_reference() -> None:
    rows = [
        execution("z-first", 10, "feature/a", "a", [0], covered=[0, 1]),
        execution("a-second", 10, "feature/a", "b", [0, 1], covered=[0, 1]),
    ]
    result = build_history_insights(
        SCOPE, WINDOW, [r for r, _ in rows], [c for _, cases in rows for c in cases]
    )
    assert result.runs[0].run_id == "a-second"
    assert result.runs[0].trend_previous_run_id == "z-first"
    assert result.runs[0].trend_pass_rate_delta == 50


def test_settings_filter_groups_versions_and_agent_across_contexts() -> None:
    rows = [
        execution(
            str(i),
            10 + i,
            "feature/a",
            str(i),
            [0],
            covered=[0],
            config={"stage": stage, "target_environment": target, "agent_name": agent},
        )
        for i, (stage, target, agent) in enumerate(
            [("dev", "east", "agent-a"), ("prod", "west", "agent-a"), ("prod", "west", "agent-b")]
        )
    ]
    result = build_history_insights(
        SCOPE.model_copy(update={"all_stages": True, "all_targets": True}),
        WINDOW,
        [r for r, _ in rows],
        [c for _, cs in rows for c in cs],
    )
    assert len(result.settings_groups) == 2
    group = next(g for g in result.settings_groups if "Agent agent-a" in g.label)
    assert group.run_ids == ["0", "1"]
    assert len(group.cohort_keys) == 2
    assert group.label == "BSA 3.14 · Skill 0.8.0 · Agent agent-a"
    assert len(result.cohorts) == 3
    assert len(result.branches) == 2
    assert result.model_dump(by_alias=True)["settingsGroups"][0]["runIds"] == ["0", "1"]
