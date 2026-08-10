# Eval Control API

Read-only FastAPI service for the Eval Control Center. Unit and E2E evaluations share one MongoDB database but use four explicit collections:

- `unit_eval_runs` and `unit_eval_cases`
- `e2e_eval_runs` and `e2e_eval_cases`

A unit run is constrained to exactly one skill. The comparison endpoint defaults to the latest completed version of that skill versus its previous distinct version.

## Run locally

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python scripts/create_indexes.py
uvicorn app.main:app --reload --port 8080
```

Open `http://localhost:8080/docs`. Configure the frontend with:

```bash
NEXT_PUBLIC_EVAL_API_URL=http://localhost:8080/api/v1
```

## Read API

- `GET /api/v1/unit/runs`
- `GET /api/v1/unit/runs/{run_id}`
- `GET /api/v1/unit/runs/{run_id}/cases`
- `GET /api/v1/unit/skills/{skill}/versions`
- `GET /api/v1/unit/skills/{skill}/comparison`
- `GET /api/v1/e2e/runs`
- `GET /api/v1/e2e/runs/{run_id}`
- `GET /api/v1/e2e/runs/{run_id}/cases`
- `GET /health/live` and `GET /health/ready`

All list endpoints use bounded opaque cursor pagination. Common filters map to indexed fields; run list documents do not embed cases.

## Reliability and scaling

- One async Mongo client/pool per process, opened and closed by FastAPI lifespan.
- Retryable reads and bounded connection/server-selection settings.
- One Uvicorn process per Kubernetes pod; horizontal scaling is handled by the included HPA.
- Liveness does not depend on MongoDB; readiness does.
- Explicit CORS origins, request correlation IDs, non-root container, resource limits and PDB.
- `AuthenticationPlaceholderMiddleware` is the insertion point for later SSO/OIDC validation.

Replace the image, Mongo URI, CORS origin, and secret management in `deploy/kubernetes.yaml` before applying it.
