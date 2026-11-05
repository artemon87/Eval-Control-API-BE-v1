# Dummy unit evaluation dataset

`scripts/dummy_unit_data.py` generates new synthetic data, without reading or
modifying real evaluations. Defaults: 5,000 runs plus 25,000 cases, five skills,
10 branches, 90 days, actor `artem.kovtunenko`, category `dummy`, and reproducible
random seed 42. Change actor with `--actor YOUR_ACTOR`. It uses a clearly synthetic
repository `dummy/evalhub-performance` and branch prefix `dummy/perf-branch-`.
Case IDs repeat across executions and branches; Mongo `_id` and run IDs are unique.
All runs are completed with mixed pass/fail outcomes, and include configuration,
versions, stage/target/agent metadata, metrics, scores and thresholds. Case detail
includes representative single-/multi-turn records, not cloned private prompts.

Use the backend virtual environment with pymongo and python-dotenv installed.
Select the intended database explicitly. The URI comes from `.env` or `--env-file`;
MONGODB_URI / DS_DB_MONGODB_URI and database env values must not conflict.
Environment variables override dotenv values. Use a local disposable DB first.
No application or index creation runs as part of this script.

Preview: no connection or writes:

```bash
python -m scripts.dummy_unit_data seed --database agent-evals-east4-dev --env-file .env.dev --batch-id perf-5000
```

Insert the default dataset (5000 RUNS, 25000 CASES, not 5000 total documents):

```bash
python -m scripts.dummy_unit_data seed --database agent-evals-east4-dev --env-file .env.dev --batch-id perf-5000 --apply --confirm-database agent-evals-east4-dev
```

For local, replace BOTH database arguments with the real local DB name and select
its env file. The chosen cluster still depends on the URI. Existing collections
must exist. Inserts are bounded batches (100 runs / 500 cases by default).
A batch that already exists is refused. A failed insertion can leave partial data;
clean up that batch before retrying. Use a distinct batch ID for independent datasets.
Do not run concurrent seeders with the same batch ID.

Preview cleanup counts (connects, reads only):

```bash
python -m scripts.dummy_unit_data cleanup --database agent-evals-east4-dev --env-file .env.dev --batch-id perf-5000
```

Apply cleanup:

```bash
python -m scripts.dummy_unit_data cleanup --database agent-evals-east4-dev --env-file .env.dev --batch-id perf-5000 --apply --confirm-database agent-evals-east4-dev
```

Both collections carry top-level `category: dummy`, `dummy_data: true` and
`dummy_batch_id`. Runs also have `unit_config.category: dummy`; cases have case_meta.
Cleanup uses ALL THREE top-level markers, deletes cases first, and can be rerun
following interruption. Do not delete all documents just because their category is dummy.

For comparisons, keep the dataset unchanged while measuring before/after indexes.
Select category dummy and the same time window, repository, skill and branch each
time. Compare several warm requests in browser Network tools, recording response
time and payload size; measure client rendering separately. These documents have
representative small payloads, not production-sized trajectories. Indexes can help
DB queries but cannot remove full-result transfers or browser rendering costs.
The script neither modifies indexes nor fabricates GitHub workflow URLs.

No database seeding was performed during development; tests use generated data and
mocked database methods. Shared dev data affects global Overview totals until removed.
