# EvalHub k6 read API tests

Read-only load testing for the EvalHub backend through the frontend proxy (`/api/eval` by default).

## Running

```bash
TEST_CONFIG=./scripts/k6/config.json TEST_NAME=smoke \
BASE_URL=http://localhost:3000 \
k6 run scripts/k6/evalhub_read_api.js
```

For backend-direct runs use `BASE_URL=http://localhost:8000 EVAL_API_PREFIX=/api/eval` (adjust to the deployed backend API prefix).

Set `TEST_NAME` to `smoke`, `average_load`, `stress`, or `spike`. Supported optional environment overrides: `CATEGORY`, `STAGE`, `TARGET_ENVIRONMENT`, `ENVIRONMENT`, `AGENT_NAME`, `ACCESS_TOKEN`, `SLEEP_SECONDS` and `PAGE_CASES=1`. If `TEST_CONFIG` is absent, the script uses a basic smoke profile. Configure `TEST_CONFIG` as an absolute path if the CI working directory differs.

This script **never uploads images, downloads images, votes, creates updates, or mutates state**. Product update images are intentionally excluded while GCS service-account permissions are unresolved.

Filters and history use sampled existing records; empty datasets can skip certain endpoint calls. Inspect per-endpoint sample counts before treating thresholds as coverage. Stage values may be set explicitly through environment variables. Comparison reads and unit-case trends can be added when test fixtures with known matching records are available.

The thresholds in config are carried forward from the supplied CI setup as baseline starting points, not evidence of current response-time SLOs. Validate and tighten after measuring real baseline traffic.

## CI note

The existing CI config screenshot uses a wrapper-specific `tests` format. This script explicitly reads it from `TEST_CONFIG`, with `TEST_NAME` selecting the profile. If the existing CI runner automatically injects options, remove the explicit config selection or pass the same environment values. Check whether the wrapper passes `TEST_NAME` under another name before migration.

For production-like tests, ensure rate limits, authorization, and approval to generate 100-VU spikes in the target environment.
