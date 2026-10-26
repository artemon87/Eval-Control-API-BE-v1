# EvalHub k6 CI script (review copy)

This directory is a review/staging location for the **existing** `ds-tools-load-testing` k6 test, not a new independent runner.

Copy:
- `scripts/k6/evalhub_read_api.js` -> `performance/services/evalhub/endpoints/eval_api/eval_api.js`
- `scripts/k6/config.json` -> `performance/services/evalhub/endpoints/eval_api/config.json`

The JS intentionally retains the original `../../../../utils/summary.js` import, the existing helper/function names, `handleSummary`, endpoint metric names, and `loadEvalApi()` default function. That import is **not present in the backend repository**, so running directly from `scripts/k6/` in this repository will fail until copied into the original CI repository.

Your CI runner already selects the named `config.json` test profile. Keep that runner as the authority rather than introducing a second execution mechanism. Confirm that `__ENV.TEST_CONFIG` is the path to k6 *options*, not the whole wrapper config; retain the working existing CI injection behavior. The screenshots do not show the surrounding runner implementation.

Original journey preserved in every iteration: Unit and E2E first pages, run details and cases, next cursor pages, and filtered Git/repository requests. Additional read-only queries rotate by iteration: Unit category/stage/target/environment/agent, trends for Unit skill/case and E2E suite/case, unit version listing, and What's New JSON. These extra requests depend on test data being available.

No GCS images, uploads, creates, deletes, voting, or writes are executed. Existing smoke/average/stress/spike stages and endpoint metric names are retained in config.json, with expanded per-endpoint thresholds.

**Validation required before merge into CI**: parse JavaScript with k6 in the original directory, run smoke, inspect all endpoint sample counts and request failures, and ensure the CI runner consumes the wrapper config correctly. No test run was performed during PR development.
