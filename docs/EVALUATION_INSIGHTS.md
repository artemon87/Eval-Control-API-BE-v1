# History Insights API

`GET /history/insights` (under the configured API prefix) serves the existing
Insights heatmap, test matrix, KPIs, cards and timelines for unit and E2E runs.
The browser owns presentation, selection, highlighting, column sizing and local
table search. The backend owns scope defaults, record selection and analysis.

Parameters: `eval_type=unit|e2e`, `range=7d|30d|90d`, optional UTC `window_end`,
`repository` (default `all`), optional `category`, `stage`, `scope`, `baseline`,
and `target_environment` (default `all`). Invalid parameters return 422; a future
anchor returns 400. Routes use the application's existing middleware and
repository dependencies.

The backend resolves `[startedFrom, startedBefore)` using its clock. The response
contains those UTC boundaries, available controls, resolved filters, branch and
scope rows, classified cells, ranked cards, counts and observation timelines.
Cell maps use the existing `row + NUL + branch` identity. Raw prompts, responses
and tool traces are excluded from database projections and API responses.

`GET /history/window?range=30d&window_end=...` exposes the same resolver for unit
Trend Explorer and E2E History. Those clients pass the returned boundaries
unchanged to existing queries/pagination. Range and scope changes retain the
server anchor; Refresh requests a new anchor. Other Overview/run-list window
behavior is outside this change.

## Coverage and limits

Every eligible run in the requested window and every case record for the selected
scope participates. The former browser limits (1,000 runs, four case runs per
branch and 48 case runs total) are removed. Repository queries cap processing at
10,000 raw window runs and 100,000 scope case records. Exceeding either cap
returns 413 asking for a narrower window/scope; no partial analytics are returned.
Case IDs are queried in batches of 500. Existing `evaluation_time_window` and
`run_cases` indexes support the queries; no new index is required.

`coverage=complete` means the query was not truncated. It does not assert that
upstream ingestion produced every expected case record or establish configuration
or test-set equivalence.

## Rules and rollout

`rulesVersion=insights-v1` preserves the supplied Insights classification policy,
including conventional baseline preference, five-point regression differences,
ten-point environment gaps, same-context flip counting, baseline context fallback,
and its error/skip/XPASS accounting. These heuristic labels do not establish
branch causality. This migration does not silently substitute Trend Explorer's
stricter evidence-comparison rules for the supplied Insights rules. Some card
explanations use more cautious wording. Full-window analysis can change counts
and classifications compared with the previous browser sample.

Deploy this backend after its parent History PR. Then deploy the paired frontend;
it requires both new endpoints and reports API errors instead of calculating
browser-side fallback results. The server resolves all History presets; clients
continue to display and retain returned boundaries.
