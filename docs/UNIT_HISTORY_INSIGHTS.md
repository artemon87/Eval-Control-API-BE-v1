# Unit History insights

The unit History page is scoped to one skill/repository, stage, target, execution environment,
and fixed half-open UTC window. It summarizes observed branch evolution and case patterns;
it does not infer that a recorded commit caused a change.

## API and rollout

- `GET /unit/history/scopes` lists distinct recorded scopes across the full run collection.
  Choices do not disappear when a short time window contains no runs.
- `GET /unit/history/insights?scope_key=...&started_from=...&started_before=...` loads insights.
  Both bounds are required; the window must be positive and at most 90 days.
- `cohort_key` optionally selects an analysis configuration returned in `cohorts`.
- Scope keys encode exact scope values, including null repository/target values. Expressions
  treat those values as literals; a missing value never means “all targets.” Legacy stage
  values fall back to the recorded top-level environment, matching run mapping.
- Run `python -m scripts.create_indexes` using the deployment's normal database access
  before rollout. The added `unit_skill_insight_window` index supports the skill/time scan.
- Deploy this backend before the frontend History insights change. No data migration is needed.

## Evidence and denominators

The repository projects bounded run metadata, then aggregates only case identity, verdict,
metric names, thresholds, and record counts in batches of 500 run IDs. Prompts, responses,
and tool traces are not loaded. There is no 200-run truncation. Supporting run metadata covers
all selected executions; the browser paginates its display.

A case identity uses `test_name` plus `test_type`, falling back to `case_id` when the name is
missing. Repeated records for the same identity within an execution count as one execution;
a failed verdict remains a failure. Blocked/error, skipped, pending, and XPASS are distinct
from failed test verdicts.

Frequency analysis uses completed runs whose case record count agrees with the summary total.
It groups recorded runtime/evaluator settings, metric and threshold profiles. Skill version,
actor, and Git SHA do not define a configuration. Intentional test subsets contribute only
their observed cases: the denominator is executions where that case actually ran. Missing or
incomplete case records are not evidence of a pass, failure, or intentionally omitted case.

“Latest result” uses each branch's latest execution. An absent case is “Not run” only when
that execution's case records are complete; otherwise it is “Unavailable.” A marker identifies
latest executions outside the selected analysis configuration. Case filters use latest failed
verdicts in this view, and period-wide failed observations in “Failure frequency.”

## Branch and repeatability signals

Branch direction requires distinct recorded SHAs, complete configuration metadata, and the
same case identities and recorded definitions at the first and latest complete executions.
An incomplete latest execution cannot establish improvement. Missing Git refs and SHAs stay
explicit and never create fake updates or repeated-run groups.

“Inconsistent repeat runs” requires passed and failed outcomes at the same branch/SHA,
configuration, and complete case set. It reports observed disagreement, not a diagnosis of
flakiness. “Fewer cases tested” requires an earlier complete, strictly larger case set on that
branch with matching definitions for shared cases. It may reflect an intentional subset.

Actor is the recorded run triggerer, not necessarily the commit author. Ref/SHA refer to the
repository recorded by the producer; the service does not fetch Git ancestry, PR merge state,
or commit authorship. Matching case identities and recorded thresholds do not guarantee
identical prompt/test content; strict content equivalence would require a persisted test
snapshot fingerprint from the producer.
