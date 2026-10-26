# EvalHub k6 drop-in replacement for existing CI

This is a **copy into the existing performance test repository**, not a second deployment or an independent CI suite.

Replace only these existing files in `ds-tools-load-testing`:

| From backend review PR | Destination in load-testing repo |
| --- | --- |
| `scripts/k6/evalhub_read_api.js` | `performance/services/evalhub/endpoints/eval_api/eval_api.js` |
| `scripts/k6/config.json` | `performance/services/evalhub/endpoints/eval_api/config.json` |

**Do not copy this backend repo's directory structure.** The source script imports `../../../../utils/summary.js` as in the original CI layout, which is deliberately not present here. Consequently it cannot run inside this BE repo without that utility.

The original helper names and all original requests run every iteration: `parseJson`, `get`, `isPage`, `isRun`, `firstRun`, `addParameter`, `runFilterQuery`, `loadFirstPages`, `loadRunDetails`, `loadNextPage`, `loadFilteredRuns`, `loadEvalApi`, plus existing `handleSummary`.

New, **read-only** requests rotate through category/stage/target/agent filters, E2E stage+target, skill and case history, versions and What's New metadata. Neither GCS images nor any POST/PATCH/DELETE/upload requests are sent. Conditional request samples can be absent if the backing MongoDB collections don't contain qualifying records.

The CI JSON retains the `script`, `tags`, `tests` wrapper and four named profiles, now naming `eval_api.js`. The script supports selecting an options profile when passed `TEST_CONFIG=<path>/config.json` and `TEST_NAME=smoke|average_load|stress|spike`, but the current CI runner may separately inject k6 options; preserve any existing working injection.

## Validation before using in CI

1. Copy both files into the **existing** directory; keep the utility file unchanged.
2. Run the original smoke pipeline with the same host/authentication and config profile settings.
3. Verify `unit-runs`, `e2e-runs`, details/cases, filtered runs and the new endpoints report samples.
4. Check endpoint p95 and HTTP errors; tests skipped due to absent fixtures need to be called out.
5. Only then run average/stress/spike against an approved staging host.

The files have not been executed in your original CI environment. The original full text was available through screenshots, not a connected source repository; verify helper and CI wrapper behavior against your working checkout before overwriting it.
