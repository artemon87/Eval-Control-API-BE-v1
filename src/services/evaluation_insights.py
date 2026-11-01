"""Server-owned analysis for the History Insights view (unit and E2E)."""

import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from src.models.evaluation_insights import InsightsQuery

REGRESSION_POINTS = 5
ENVIRONMENT_GAP_POINTS = 10
UNRECORDED_BRANCH = "(no branch recorded)"
Row = dict[str, Any]


def cell_key(row: str, branch: str) -> str:
    return f"{row}\0{branch}"


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0


def names(values: list[str], limit: int = 3) -> str:
    return ", ".join(values[:limit]) + (
        f" +{len(values) - limit} more" if len(values) > limit else ""
    )


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def run_branch(run: Row) -> str:
    ref = (run.get("gitRef") or "").strip()
    pull = re.match(r"^refs/pull/(\d+)/", ref)
    return (
        f"PR #{pull[1]}" if pull else re.sub(r"^refs/(heads|tags)/", "", ref) or UNRECORDED_BRANCH
    )


def run_scope(run: Row) -> str:
    return str(run["target"])


def run_context(run: Row) -> str:
    return str(run["context"])


def pick_baseline(runs: list[Row]) -> str | None:
    counts = Counter(run_branch(run) for run in runs)
    for candidate in ("main", "master", "develop", "dev"):
        if candidate in counts:
            return candidate
    ranked = sorted((b for b in counts if b != UNRECORDED_BRANCH), key=lambda b: (-counts[b], b))
    return ranked[0] if ranked else next(iter(counts), None)


def failing(outcome: str) -> bool:
    # Preserve the supplied Insights outcome policy during this architecture migration.
    return outcome in {"fail", "error"}


def normalized_verdict(value: Any) -> str:
    value = str(value or "").strip().lower()
    aliases = {
        "pass": "passed",
        "fail": "failed",
        "block": "blocked",
        "error": "blocked",
        "xpass": "xpassed",
        "unexpected_pass": "xpassed",
        "unexpected-pass": "xpassed",
    }
    return (
        aliases.get(value, value)
        if value in aliases or value in {"passed", "failed", "blocked", "skipped", "xpassed"}
        else "pending"
    )


def outcome(verdict: str) -> str:
    return {"passed": "pass", "xpassed": "pass", "failed": "fail", "skipped": "skip"}.get(
        normalized_verdict(verdict), "error"
    )


def number(value: Any) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def rank(insights: list[Row]) -> list[Row]:
    return sorted(
        insights,
        key=lambda row: {"critical": 0, "warning": 1, "good": 2, "info": 3}[row["severity"]],
    )


def add(
    insights: list[Row],
    identity: str,
    severity: str,
    kind: str,
    title: str,
    detail: str,
    **focus: Any,
) -> None:
    insights.append(
        dict(id=identity, severity=severity, kind=kind, title=title, detail=detail, **focus)
    )


def build_landscape(runs: list[Row], baseline: str | None, eval_type: str) -> Row:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for run in runs:
        grouped[cell_key(run_scope(run), run_branch(run))].append(run)
    cells: dict[str, Row] = {}
    for key, values in grouped.items():
        ordered = sorted(values, key=lambda r: r["startedAt"])
        latest = ordered[-1]
        cells[key] = dict(
            scope=run_scope(latest),
            branch=run_branch(latest),
            runs=ordered,
            latest=latest,
            latestPassRate=latest["summary"]["passRatePct"],
            meanPassRate=mean([r["summary"]["passRatePct"] for r in ordered]),
            deltaVsBaseline=None,
            regressed=False,
            improved=False,
        )
    for cell in cells.values():
        base = (
            cells.get(cell_key(cell["scope"], baseline))
            if baseline and cell["branch"] != baseline
            else None
        )
        if not base:
            continue
        matching = [r for r in base["runs"] if run_context(r) == run_context(cell["latest"])]
        reference = matching[-1] if matching else base["latest"]
        delta = cell["latestPassRate"] - reference["summary"]["passRatePct"]
        cell.update(
            deltaVsBaseline=delta,
            regressed=delta <= -REGRESSION_POINTS,
            improved=delta >= REGRESSION_POINTS,
        )
    scopes: list[Row] = []
    rows_by_scope: dict[str, list[Row]] = defaultdict(list)
    rows_by_branch: dict[str, list[Row]] = defaultdict(list)
    for cell in cells.values():
        rows_by_scope[cell["scope"]].append(cell)
        rows_by_branch[cell["branch"]].append(cell)
    for scope, scope_cells in rows_by_scope.items():
        base = cells.get(cell_key(scope, baseline)) if baseline else None
        worst = min(scope_cells, key=lambda c: c["latestPassRate"])
        scopes.append(
            dict(
                scope=scope,
                baselinePassRate=base["latestPassRate"] if base else None,
                branchesRegressed=sum(c["regressed"] for c in scope_cells),
                worstBranch=dict(branch=worst["branch"], passRate=worst["latestPassRate"]),
                meanPassRate=mean([c["latestPassRate"] for c in scope_cells]),
                runCount=sum(len(c["runs"]) for c in scope_cells),
                systemic=len(scope_cells) >= 2
                and all(c["latest"]["verdict"] != "passed" for c in scope_cells),
            )
        )
    scopes.sort(key=lambda r: (-r["branchesRegressed"], r["meanPassRate"], r["scope"]))
    branches: list[Row] = []
    for branch, branch_cells in rows_by_branch.items():
        branch_runs = [r for c in branch_cells for r in c["runs"]]
        branches.append(
            dict(
                branch=branch,
                isBaseline=branch == baseline,
                scopesRegressed=sum(c["regressed"] for c in branch_cells),
                scopesImproved=sum(c["improved"] for c in branch_cells),
                runCount=len(branch_runs),
                meanPassRate=mean([c["latestPassRate"] for c in branch_cells]),
                lastRunAt=max(r["startedAt"] for r in branch_runs),
                actors=list(
                    dict.fromkeys(
                        r["actor"]
                        for r in branch_runs
                        if r.get("actor") and r["actor"] != "unknown"
                    )
                ),
            )
        )
    branches.sort(key=lambda c: c["lastRunAt"], reverse=True)
    branches.sort(key=lambda c: (not c["isBaseline"], -c["scopesRegressed"]))
    noun = "skill" if eval_type == "unit" else "target"
    insights: list[Row] = []
    for row in scopes:
        if row["systemic"]:
            add(
                insights,
                f"systemic:{row['scope']}",
                "critical",
                "systemic",
                f"{row['scope']} is failing on every branch"
                + (f", including {baseline}" if baseline else ""),
                (
                    f"Average latest pass rate is {row['meanPassRate']:.0f}%. "
                    f"Review shared {noun} behavior and test data."
                ),
                scope=row["scope"],
            )
    for row in [r for r in scopes if r["branchesRegressed"] and not r["systemic"]][:4]:
        regressed = sorted(
            [c for c in cells.values() if c["scope"] == row["scope"] and c["regressed"]],
            key=lambda c: c["deltaVsBaseline"],
        )
        worst = regressed[0]
        add(
            insights,
            f"scope-regression:{row['scope']}",
            "critical" if len(regressed) > 1 else "warning",
            "branch-regression",
            f"{row['scope']} regressed on {plural(len(regressed), 'branch')}",
            (
                f"Worst is {worst['branch']} at {worst['latestPassRate']:.0f}% "
                f"vs {row['baselinePassRate']:.0f}% on {baseline}. "
                f"Affected: {names([c['branch'] for c in regressed])}."
            ),
            scope=row["scope"],
            branch=worst["branch"],
        )
    for column in [c for c in branches if not c["isBaseline"] and c["scopesRegressed"] >= 2][:3]:
        add(
            insights,
            f"blast:{column['branch']}",
            "critical",
            "branch-regression",
            f"{column['branch']} drags down {plural(column['scopesRegressed'], noun)}",
            (
                f"Broad blast radius: at least {REGRESSION_POINTS} "
                f"points below {baseline} on {plural(column['scopesRegressed'], noun)}."
            ),
            branch=column["branch"],
        )
    drift = [
        (c, c["runs"][0]["summary"]["passRatePct"] - c["latestPassRate"])
        for c in cells.values()
        if c["branch"] == baseline and len(c["runs"]) >= 3
    ]
    for cell, drop in sorted(drift, key=lambda x: -x[1])[:2]:
        if drop >= REGRESSION_POINTS * 2:
            add(
                insights,
                f"drift:{cell['scope']}",
                "warning",
                "baseline-drift",
                f"{cell['scope']} is sliding on {baseline}",
                f"Down {drop:.0f} points over {plural(len(cell['runs']), 'run')} in this window.",
                scope=cell["scope"],
                branch=baseline,
            )
    for column in sorted(
        [c for c in branches if not c["isBaseline"] and c["scopesImproved"]],
        key=lambda c: -c["scopesImproved"],
    )[:2]:
        add(
            insights,
            f"improve:{column['branch']}",
            "good",
            "branch-fix",
            f"{column['branch']} improves {plural(column['scopesImproved'], noun)}",
            f"Scores at least {REGRESSION_POINTS} points above {baseline}. Worth reviewing.",
            branch=column["branch"],
        )
    gaps: dict[str, Row] = {}
    for cell in cells.values():
        contexts = {run_context(r): r["summary"]["passRatePct"] for r in cell["runs"]}
        if len(contexts) < 2:
            continue
        ordered_contexts = sorted(contexts, key=lambda c: contexts[c])
        spread = contexts[ordered_contexts[-1]] - contexts[ordered_contexts[0]]
        if spread < ENVIRONMENT_GAP_POINTS:
            continue
        current = gaps.get(cell["scope"])
        if (
            not current
            or (cell["branch"] == baseline and current["branch"] != baseline)
            or (current["branch"] != baseline and spread > current["spread"])
        ):
            gaps[cell["scope"]] = dict(
                scope=cell["scope"],
                branch=cell["branch"],
                spread=spread,
                best=ordered_contexts[-1],
                worst=ordered_contexts[0],
                contexts=contexts,
            )
    for gap in sorted(gaps.values(), key=lambda g: -g["spread"])[:3]:
        add(
            insights,
            f"environment:{gap['scope']}",
            "critical" if gap["spread"] >= 25 else "warning",
            "environment-gap",
            (
                f"{gap['scope']} scores {gap['contexts'][gap['worst']]:.0f}% "
                f"on {gap['worst']} but {gap['contexts'][gap['best']]:.0f}% "
                f"on {gap['best']}"
            ),
            (
                f"Same branch ({gap['branch']}), latest run in "
                f"each environment. Pick a Stage and Target for "
                f"a like-for-like comparison."
            ),
            scope=gap["scope"],
            branch=gap["branch"],
        )
    return dict(scopes=scopes, branches=branches, cells=cells, insights=rank(insights))


def count_flips(observations: list[Row]) -> int:
    before: dict[str, bool] = {}
    flips = 0
    for observation in observations:
        context = observation["context"]
        state = failing(observation["outcome"])
        flips += context in before and before[context] != state
        before[context] = state
    return flips


def environment_split(row: list[Row], baseline: str | None) -> Row | None:
    splits: list[Row] = []
    for cell in row:
        contexts: dict[str, list[Row]] = defaultdict(list)
        for observation in cell["observations"]:
            contexts[observation["context"]].append(observation)
        fails = sorted(c for c, obs in contexts.items() if all(failing(o["outcome"]) for o in obs))
        passes = sorted(
            c for c, obs in contexts.items() if all(o["outcome"] == "pass" for o in obs)
        )
        fail_count = sum(len(contexts[c]) for c in fails)
        if fails and passes and fail_count >= 2:
            splits.append(
                dict(
                    branch=cell["branch"],
                    failing=fails,
                    passing=passes,
                    weight=len(cell["observations"]),
                )
            )
    if not splits:
        return None
    splits.sort(key=lambda s: (s["branch"] != baseline, -s["weight"]))
    return {k: v for k, v in splits[0].items() if k != "weight"}


def build_test_matrix(runs: list[Row], cases: list[Row], baseline: str | None) -> Row:
    by_run: dict[str, list[Row]] = defaultdict(list)
    for item in cases:
        by_run[item["run_id"]].append(item)
    cells: dict[str, Row] = {}
    labels: dict[str, Row] = {}
    loaded: Counter[str] = Counter()
    for run in sorted(runs, key=lambda r: r["startedAt"]):
        branch = run_branch(run)
        loaded[branch] += 1
        for item in by_run[run["runId"]]:
            group = item.get("suite") or item.get("skill") or "Uncategorized"
            label = item.get("test_name") or item.get("case_id") or "Unnamed test"
            test_key = f"{group}::{label}"
            labels.setdefault(test_key, dict(label=label, group=group))
            key = cell_key(test_key, branch)
            cell = cells.setdefault(key, dict(testKey=test_key, branch=branch, observations=[]))
            scores = [number(value) for value in (item.get("scores") or {}).values()]
            cell["observations"].append(
                dict(
                    runId=run["runId"],
                    startedAt=run["startedAt"],
                    context=run_context(run),
                    gitSha=run.get("gitSha"),
                    outcome=outcome(item.get("verdict", "pending")),
                    score=mean(scores) if scores else number(item.get("score")),
                )
            )
    for cell in cells.values():
        obs = cell["observations"]
        cell.update(
            latest=obs[-1],
            passes=sum(o["outcome"] == "pass" for o in obs),
            failures=sum(failing(o["outcome"]) for o in obs),
            flips=count_flips(obs),
            state="fail" if failing(obs[-1]["outcome"]) else "pass",
        )
    for cell in cells.values():
        base = (
            cells.get(cell_key(cell["testKey"], baseline))
            if baseline and baseline != cell["branch"]
            else None
        )
        reference = None
        if base:
            matches = [o for o in base["observations"] if o["context"] == cell["latest"]["context"]]
            reference = matches[-1] if matches else base["latest"]
        fail = failing(cell["latest"]["outcome"])
        base_fail = failing(reference["outcome"]) if reference else False
        if cell["flips"] >= 2:
            cell["state"] = "flaky"
        elif base and base["flips"] < 2:
            if fail and not base_fail:
                cell["state"] = "regression"
            elif not fail and base_fail:
                cell["state"] = "fixed"
    tests: list[Row] = []
    rows_by_test: dict[str, list[Row]] = defaultdict(list)
    rows_by_branch: dict[str, list[Row]] = defaultdict(list)
    for cell in cells.values():
        rows_by_test[cell["testKey"]].append(cell)
        rows_by_branch[cell["branch"]].append(cell)
    for key, label in labels.items():
        row = rows_by_test[key]
        failing_cells = [c for c in row if failing(c["latest"]["outcome"])]
        base = cells.get(cell_key(key, baseline)) if baseline else None
        base_fail = bool(base and failing(base["latest"]["outcome"]))
        regressions = [c for c in row if c["state"] == "regression"]
        split = environment_split(row, baseline)
        tests.append(
            dict(
                testKey=key,
                **label,
                failingBranches=len(failing_cells),
                branchesWithData=len(row),
                regressions=len(regressions),
                fixes=sum(c["state"] == "fixed" for c in row),
                flaky=any(c["flips"] >= 2 for c in row),
                failsOnBaseline=base_fail,
                bottleneck=not split
                and len(row) >= 2
                and len(failing_cells) >= max(2, math.ceil(len(row) / 2))
                and (base_fail or not base),
                branchSpecific=regressions[0]["branch"]
                if len(regressions) == len(failing_cells) == 1
                else None,
                environmentSplit=split,
                failureCount=sum(c["failures"] for c in row),
                history=sorted(
                    [o for c in row for o in c["observations"]], key=lambda o: o["startedAt"]
                ),
            )
        )
    tests.sort(
        key=lambda r: (
            not r["bottleneck"],
            -r["failingBranches"],
            -r["regressions"],
            -r["failureCount"],
            r["label"],
        )
    )
    branches = []
    for branch, count in loaded.items():
        column = rows_by_branch[branch]
        branches.append(
            dict(
                branch=branch,
                isBaseline=branch == baseline,
                regressions=sum(c["state"] == "regression" for c in column),
                fixes=sum(c["state"] == "fixed" for c in column),
                passRate=100
                * sum(not failing(c["latest"]["outcome"]) for c in column)
                / len(column)
                if column
                else 0,
                testsRun=len(column),
                runsLoaded=count,
            )
        )
    branches.sort(
        key=lambda c: (not c["isBaseline"], -c["regressions"], c["passRate"], c["branch"])
    )
    total = sum(r["failureCount"] for r in tests)
    insights = test_insights(tests, branches, cells, baseline, total)
    return dict(
        tests=tests, branches=branches, cells=cells, totalFailures=total, insights=rank(insights)
    )


def test_insights(
    tests: list[Row], branches: list[Row], cells: dict[str, Row], baseline: str | None, total: int
) -> list[Row]:
    insights: list[Row] = []
    for row in [r for r in tests if r["bottleneck"]][:3]:
        add(
            insights,
            f"bottleneck:{row['testKey']}",
            "critical",
            "bottleneck",
            (
                f"{row['label']} fails on {row['failingBranches']} "
                f"of {row['branchesWithData']} branches"
            ),
            f"Fails on {baseline} too; review this shared bottleneck."
            if row["failsOnBaseline"]
            else "Fails across most branches. Review shared behavior and test data.",
            testKey=row["testKey"],
        )
    for col in [c for c in branches if not c["isBaseline"] and c["regressions"]][:3]:
        broken = [
            r
            for r in tests
            if cells.get(cell_key(r["testKey"], col["branch"]), {}).get("state") == "regression"
        ]
        only_here = [r for r in broken if r["branchSpecific"] == col["branch"]]
        add(
            insights,
            f"branch-regression:{col['branch']}",
            "critical" if col["regressions"] > 1 else "warning",
            "branch-regression",
            f"{col['branch']} breaks {plural(col['regressions'], 'test')} that pass on {baseline}",
            (
                f"Only this branch fails {names([r['label'] for r in only_here])}. "
                f"Start the investigation in this branch's diff."
            )
            if only_here
            else (
                f"Broken here: {names([r['label'] for r in broken])}. "
                f"Other branches fail these too."
            ),
            branch=col["branch"],
        )
    for col in sorted(
        [c for c in branches if not c["isBaseline"] and c["fixes"]], key=lambda c: -c["fixes"]
    )[:2]:
        add(
            insights,
            f"branch-fix:{col['branch']}",
            "good",
            "branch-fix",
            f"{col['branch']} fixes {plural(col['fixes'], 'test')} failing on {baseline}",
            "Passes where the baseline fails. Worth reviewing.",
            branch=col["branch"],
        )
    flaky = [r for r in tests if r["flaky"]]
    if flaky:
        add(
            insights,
            "flaky",
            "warning",
            "flaky",
            (
                f"{plural(len(flaky), 'test')} {'flips' if len(flaky) == 1 else 'flip'} "
                f"between pass and fail on the same branch"
            ),
            (
                f"{names([r['label'] for r in flaky])}. Review "
                f"stability before attributing changes to a branch."
            ),
            testKey=flaky[0]["testKey"],
        )
    if total >= 5:
        ordered = sorted(tests, key=lambda r: -r["failureCount"])
        covered, count = 0, 0
        while count < len(ordered) and covered < total * 0.6:
            covered += ordered[count]["failureCount"]
            count += 1
        if 0 < count <= max(1, len(tests) // 4):
            add(
                insights,
                "concentration",
                "info",
                "concentration",
                (
                    f"{plural(count, 'test')} {'accounts' if count == 1 else 'account'} "
                    f"for {math.floor(covered / total * 100 + 0.5)}% "
                    f"of all failures"
                ),
                f"Failures are concentrated: {names([r['label'] for r in ordered[:count]])}.",
                testKey=ordered[0]["testKey"],
            )
    splits: dict[str, list[Row]] = defaultdict(list)
    for row in tests:
        if row["environmentSplit"]:
            splits[", ".join(row["environmentSplit"]["failing"])].append(row)
    for failed_on, rows in sorted(splits.items(), key=lambda x: -len(x[1]))[:2]:
        example = rows[0]["environmentSplit"]
        add(
            insights,
            f"environment:{failed_on}",
            "critical" if len(rows) > 1 else "warning",
            "environment-gap",
            f"{plural(len(rows), 'test')} fail{'s' if len(rows) == 1 else ''} only on {failed_on}",
            (
                f"{names([r['label'] for r in rows])}. Passes "
                f"on {names(example['passing'], 2)} on the same "
                f"branch ({example['branch']}). Review that environment's "
                f"data or config."
            ),
            testKey=rows[0]["testKey"],
            branch=example["branch"],
        )
    coverage = max((c["testsRun"] for c in branches), default=0)
    for col in [c for c in branches if 0 < c["testsRun"] < coverage * 0.6][:1]:
        add(
            insights,
            f"coverage:{col['branch']}",
            "info",
            "coverage",
            f"{col['branch']} only ran {col['testsRun']} of {coverage} tests",
            "Its pass rate is not directly comparable. Check the selected mode or metrics.",
            branch=col["branch"],
        )
    return insights


def normalize_run(raw: Row, eval_type: str) -> Row:
    config = raw.get("unit_config") or {}
    stage = (
        (config.get("stage") or raw.get("environment") or "unknown")
        if eval_type == "unit"
        else raw.get("stage", "unknown")
    )
    target = config.get("target_environment") or "Target not recorded"
    at = raw.get("started_at")
    if isinstance(at, datetime):
        at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
        at = at.isoformat()
    return dict(
        runId=raw["run_id"],
        evalType=eval_type,
        target=raw.get("skill") if eval_type == "unit" else raw.get("target"),
        stage=stage,
        context=stage if eval_type == "e2e" else f"{stage} · {target}",
        category=config.get("category") or "Uncategorized",
        targetEnvironment=target,
        githubRepository=raw.get("github_repository"),
        gitRef=raw.get("git_ref"),
        gitSha=raw.get("git_sha"),
        actor=raw.get("actor") or "unknown",
        startedAt=at,
        verdict="blocked"
        if raw.get("execution_status") in {"blocked", "error"}
        else normalized_verdict(raw.get("verdict")),
        executionStatus=raw.get("execution_status"),
        summary=dict(
            total=number((raw.get("summary") or {}).get("total", 0)),
            passRatePct=number((raw.get("summary") or {}).get("pass_rate_pct", 0)),
        ),
    )


def select_runs(raw_runs: list[Row], query: InsightsQuery) -> Row:
    typed = [
        normalize_run(r, query.eval_type)
        for r in raw_runs
        if r.get("execution_status") in {"completed", "blocked", "error"}
        and number((r.get("summary") or {}).get("total", 0)) > 0
    ]
    repositories = sorted({r["githubRepository"] for r in typed if r["githubRepository"]})
    repo_runs = [
        r for r in typed if query.repository == "all" or r["githubRepository"] == query.repository
    ]
    categories = sorted({r["category"] for r in repo_runs})
    focus = next((r for r in repo_runs if r["target"] == query.scope), None)
    category = (
        "all"
        if query.eval_type != "unit"
        else query.category
        if query.category == "all" or query.category in categories
        else focus["category"]
        if focus
        else next(iter(categories), "all")
    )
    category_runs = [r for r in repo_runs if category == "all" or r["category"] == category]
    scopes = sorted({r["target"] for r in category_runs})
    stage_options = sorted(
        {
            r["stage"]
            for r in category_runs
            if query.eval_type == "e2e"
            or not query.scope
            or query.scope not in scopes
            or r["target"] == query.scope
        }
    )
    busiest = Counter(r["stage"] for r in category_runs).most_common(1)
    stage = (
        query.stage
        if query.stage
        and (query.stage in stage_options or (query.stage == "all" and query.eval_type == "unit"))
        else busiest[0][0]
        if query.eval_type == "e2e" and busiest
        else "all"
    )
    stage_runs = [r for r in category_runs if stage == "all" or r["stage"] == stage]
    targets = (
        sorted(
            {
                r["targetEnvironment"]
                for r in stage_runs
                if not query.scope or query.scope not in scopes or r["target"] == query.scope
            }
        )
        if query.eval_type == "unit"
        else []
    )
    target = query.target_environment if query.target_environment in targets else "all"
    runs = [r for r in stage_runs if target == "all" or r["targetEnvironment"] == target]
    branches = sorted({run_branch(r) for r in runs})
    baseline = query.baseline if query.baseline in branches else pick_baseline(runs)
    scope = query.scope if any(r["target"] == query.scope for r in runs) else None
    return dict(
        runs=runs,
        repositories=repositories,
        categories=categories,
        scopeOptions=scopes,
        stageOptions=stage_options,
        targetOptions=targets,
        activeCategory=category,
        activeStage=stage,
        activeTarget=target,
        activeScope=scope,
        baseline=baseline,
        branchNames=branches,
        mixedStages=len({r["stage"] for r in runs})
        if query.eval_type == "unit" and stage == "all"
        else 1,
        mixedTargets=len({r["targetEnvironment"] for r in runs})
        if query.eval_type == "unit" and target == "all"
        else 1,
    )


def build_response(selection: Row, cases: list[Row]) -> Row:
    runs, baseline = selection["runs"], selection["baseline"]
    landscape = build_landscape(runs, baseline, runs[0]["evalType"] if runs else "unit")
    scope_runs = [r for r in runs if r["target"] == selection["activeScope"]]
    matrix = build_test_matrix(scope_runs, cases, baseline) if selection["activeScope"] else None
    counts = dict(
        runCount=len(runs),
        scopeCount=len(landscape["scopes"]),
        branchCount=len(landscape["branches"]),
        regressedCells=sum(c["regressed"] for c in landscape["cells"].values()),
        systemicScopes=sum(r["systemic"] for r in landscape["scopes"]),
        caseRunCount=len(scope_runs),
        testsTracked=len(matrix["tests"]) if matrix else 0,
        bottleneckTests=sum(r["bottleneck"] for r in matrix["tests"]) if matrix else 0,
        branchRegressions=sum(c["regressions"] for c in matrix["branches"]) if matrix else 0,
        flakyTests=sum(r["flaky"] for r in matrix["tests"]) if matrix else 0,
    )
    return {
        **{k: v for k, v in selection.items() if k != "runs"},
        "landscape": landscape,
        "matrix": matrix,
        "counts": counts,
        "regressionPoints": REGRESSION_POINTS,
    }
