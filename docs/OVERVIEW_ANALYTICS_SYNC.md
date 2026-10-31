# Overview analytics synchronization draft

Adapt the company screenshot implementation to the internal History-insights branch. This is an implementation draft, not a byte-for-byte copy or a deployment.

## Review conclusions

The original issue was incomplete data: Overview and scope discovery depended on the newest run-list page. A bounded overview endpoint and independent facets are justified. Python aggregation over projected run metadata is a reasonable first step, not a database-only aggregate or a constant-cost query. It still reads every run in the window and recomputes unit means from cases. The 92-day bound limits elapsed time, not run volume.

Keep the new History insights API and UI and existing complete Compare queries. Do not restore the screenshot's 200-row Compare cap or legacy History UI.

## Adaptations in this draft

- GET /overview defaults to seven days and accepts explicit half-open UTC bounds up to 92 days. Return totals, daily rows, capped per-type inbox records and five recent runs per evaluation type. No run-page cap affects totals.
- Group previous runs before the window in MongoDB, then calculate strictly-earlier predecessors. Same-time executions do not compare against one another.
- Unit predecessor scopes additionally isolate target environment and execution environment; stage falls back to the top-level environment. The company screenshot scope omitted target environment and its fallback read a configuration field that was absent from its projection.
- Preserve shared git filters; honor unit environment, category, stage, target and agent filters. Distinct e2e_stage/e2e_target parameters avoid overloading unit's repeated stage query parameter.
- Unit mean-score enrichment runs in 500-run batches and skips null per-metric averages (preserving numeric zero). This carries the required guard from the separate null-metric fix into the stack; retain its dedicated PR when reconciling.
- GET /facets exposes recorded combinations independently of paging, plus refs, actors, triggers and thresholds. Optional time bounds constrain discovery; the default covers all history.
- Route wiring uses this repository's src/api/routes layout and create_app, rather than importing the company's absent api_v1 router layout.

## Validation and remaining work

New tests cover window validation, 501-run totals, capped inboxes, strictly earlier/tied predecessors, scope isolation, zero durations, UTC day coverage, repository query filters/batching, and null metric means. Mongo calls are mocked. Live Mongo query plans, index performance, simultaneous updates/deletions, and load are not verified. Existing indexes are defined in scripts/create_indexes.py (evaluation_time_window, run_cases, unit_skill_insight_window); no infrastructure was changed. A broad before-window grouping can still be expensive.

The company test_analytics.py was listed in screenshots but its contents were not provided. Tests here were written independently.

Full application route collection is blocked by the existing missing src.models.auth import. Do not treat pure repository tests as validation of auth or app startup. No production build/deployment was performed.

Before ready-for-review: verify deployed legacy records against response models, compare totals against representative data, run explain/load checks, review normalization semantics and recorded-score interpretation, and reconcile frontend metadata dropdowns with the newer History/Compare discovery APIs. The screenshot's fallback and truncating listAllRuns implementation are intentionally excluded.
