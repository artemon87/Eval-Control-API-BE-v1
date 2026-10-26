# Local FE k6 load test

This version is intended to replace the local `tests/load/eval-api-test.js` (JavaScript, despite being discussed as a Python version). It is separate from the company CI `eval_api.js` and has no CI JSON.

Copy `scripts/k6/local/eval-api-test.js` into `tests/load/eval-api-test.js` in the local frontend checkout.

Run the same as before:

```bash
BASE_URL=http://localhost:3000/api/eval PROFILE=smoke k6 run tests/load/eval-api-test.js
BASE_URL=http://localhost:3000/api/eval PROFILE=load k6 run tests/load/eval-api-test.js
BASE_URL=http://localhost:3000/api/eval PROFILE=spike k6 run tests/load/eval-api-test.js
```

The original smoke/load/spike stages, existing endpoint tags, 15-second HTTP timeout, and every original run-list/detail/cases/pagination/Git-filter request are retained. New calls rotate across category/stage/target/environment/agent filters, Unit/E2E history, skill versions and What's New JSON listing. No writes or GCS image requests are performed.

Optional `ACCESS_TOKEN` sends a Bearer header; omit it when testing a route that allows unauthenticated reads. To use a real frontend authenticated session, the bearer header alone may be insufficient; the frontend's session cookie/SSO flow must be configured separately.

Known caveats: the dynamic history tests skip when seed runs/cases are absent. They are only sampling real records. Sample counts need inspection. The 750ms global p95 and other thresholds mirror the screenshots and are deliberately stricter than the previous CI thresholds. No k6 live run was performed.
