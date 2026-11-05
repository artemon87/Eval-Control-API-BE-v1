# Indexes for agent-evals-east4-dev

Use `scripts/create_indexes_dev.py` for this database only. The general
`create_indexes.py` is unchanged. This script does not start the application,
import application Settings, or require GCS settings.

From the backend repository root, activate your virtual environment. Dependencies
are PyMongo and python-dotenv (also installed through the existing project dependencies).
Configure your existing MongoDB URI in a private `.env.dev` file:

```dotenv
MONGODB_URI=<your dev MongoDB connection string>
MONGODB_DATABASE=agent-evals-east4-dev
```

Do not commit the file. Exported environment variables take precedence over the file.
The script also accepts DS_DB_MONGODB_URI and DS_DB_MONGODB_NAME, but refuses
conflicting values. If your shell has local/staging Mongo variables exported,
use a fresh shell without those overrides before selecting the dev env file.
There is no default URI or localhost fallback. Confirm that your URI points to
the intended cluster; database-name confirmation alone cannot distinguish two
clusters containing databases with the same name.

Preview (read-only MongoDB metadata operations):

```bash
python -m scripts.create_indexes_dev --env-file .env.dev
```

For the supplied inventory, expect four CREATE lines:

- unit_eval_runs.evaluation_time_window
- unit_eval_runs.unit_skill_insight_window
- unit_eval_runs.unit_configuration_history
- e2e_eval_runs.evaluation_time_window

Expect KEEP lines for run_id_1, skill_version_history, verdict_history,
target_history, batch_history, run_cases and case_history as applicable.
Hidden indexes remain hidden. Check the database and connected nodes printed
by the plan, and inspect unexpected output before applying.

Apply:

```bash
python -m scripts.create_indexes_dev --env-file .env.dev --apply --confirm-database agent-evals-east4-dev
```

Preflight checks every collection before any writes. It recognizes equivalent
indexes under existing names and rejects incompatible options/names. Existing
unique run-ID indexes must already exist; this tool never introduces a missing
unique constraint. Missing nonunique indexes are built one at a time and verified.
It never drops, renames, or unhides indexes. Rerunning skips completed definitions.
If a build fails, earlier successful builds remain; inspect the new plan before
retrying. Do not run concurrent indexers. Schedule shared-DB builds during a quiet
period and monitor DB load and application health. This does not guarantee zero
impact on a busy cluster. No live database execution was performed during development.

For a container without scripts/, run this reviewed standalone file via stdin in
a dedicated indexer pod with its dev variables injected:

```bash
kubectl --context <dev-context> --namespace <namespace> exec -i <indexer-pod> -- python - --apply --confirm-database agent-evals-east4-dev < scripts/create_indexes_dev.py
```

Omit `--apply --confirm-database ...` to preview first. Do not use this dev-only
tool as a staging migration.
