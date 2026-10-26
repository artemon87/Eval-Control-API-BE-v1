/**
 * k6 load test for the EvalHub eval API.
 *
 * Exercises the read paths the EvalHub UI hits when a user browses eval runs.
 * One VU iteration walks the same journey the dashboard performs:
 *
 * 1. List the first page of unit runs and of e2e runs
 * 2. Open the newest run of each type (run detail + its cases)
 * 3. Page forward once on each run list (cursor pagination)
 * 4. Re-query each run list with git/repository filters applied
 *
 * Added coverage (read-only): unit filters, Unit/E2E history, versions,
 * and What's New JSON. No GCS image requests or writes.
 *
 * Requests are tagged with name so per-endpoint sub-metrics and thresholds
 * are reported separately in the summary.
 *
 * Auth: optional ACCESS_TOKEN bearer header; the FE proxy may use session
 * auth instead. BASE_URL is the host; /api/eval is appended by default.
 *
 * Environment variables:
 * BASE_URL, EVAL_API_PREFIX, GITHUB_REPOSITORY, GIT_REF, GIT_SHA,
 * CATEGORY, STAGE, TARGET_ENVIRONMENT, ENVIRONMENT, AGENT_NAME,
 * SLEEP_SECONDS, TEST_CONFIG, TEST_NAME, ACCESS_TOKEN.
 */
import http from "k6/http";
import { check, sleep } from "k6";

export { handleSummary } from "../../../../utils/summary.js";

// Important: ternary avoids issues when k6 inspect runs in CI.
export const options = __ENV.TEST_CONFIG
  ? JSON.parse(open(__ENV.TEST_CONFIG))
  : {};

const DEFAULT_HOST = "https://service-evalhub.ds-staging.us-east4.dev.gcp.int";
const TARGET_HOST = (__ENV.BASE_URL || DEFAULT_HOST).replace(/\/+$/, "");
const API_PREFIX =
  __ENV.EVAL_API_PREFIX !== undefined
    ? __ENV.EVAL_API_PREFIX
    : TARGET_HOST.endsWith("/api/eval")
      ? ""
      : "/api/eval";
const API_BASE = `${TARGET_HOST}${API_PREFIX}`;

const RUN_LIST_LIMIT = 50;
const CASE_LIST_LIMIT = 200;
const REQUEST_TIMEOUT = "60s";

const HEADERS = {
  Accept: "application/json",
};
if (__ENV.ACCESS_TOKEN) HEADERS.Authorization = `Bearer ${__ENV.ACCESS_TOKEN}`;

function parseJson(response) {
  try {
    return response.json();
  } catch {
    return null;
  }
}

function get(path, name, validatePayload = () => true) {
  const response = http.get(`${API_BASE}${path}`, {
    tags: { name },
    headers: HEADERS,
    timeout: REQUEST_TIMEOUT,
  });

  const body = parseJson(response);
  check(response, {
    [`${name}: status 200`]: (result) => result.status === 200,
    [`${name}: valid JSON`]: () => body !== null,
    [`${name}: expected payload`]: () => body !== null && validatePayload(body),
  });
  return response.status === 200 && body !== null && validatePayload(body) ? body : null;
}

function isPage(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    Array.isArray(value.items) &&
    Object.prototype.hasOwnProperty.call(value, "next_cursor")
  );
}

function isRun(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    typeof value.run_id === "string" &&
    value.run_id.length > 0
  );
}

function firstRun(page) {
  if (!isPage(page) || page.items.length === 0) {
    return null;
  }

  return page.items[0];
}

function addParameter(parameters, name, value) {
  if (typeof value !== "string") {
    return;
  }

  const trimmed = value.trim();
  if (trimmed.length > 0) {
    parameters.push(
      `${encodeURIComponent(name)}=${encodeURIComponent(trimmed)}`,
    );
  }
}

function runFilterQuery(run) {
  const parameters = [`limit=${RUN_LIST_LIMIT}`];

  addParameter(
    parameters,
    "github_repository",
    __ENV.GITHUB_REPOSITORY || run?.github_repository,
  );
  addParameter(parameters, "git_ref", __ENV.GIT_REF || run?.git_ref);
  addParameter(parameters, "git_sha", __ENV.GIT_SHA || run?.git_sha);

  return parameters.join("&");
}

function loadFirstPages() {
  const unitPage = get(
    `/unit/runs?limit=${RUN_LIST_LIMIT}`,
    "unit-runs",
    isPage,
  );

  const e2ePage = get(`/e2e/runs?limit=${RUN_LIST_LIMIT}`, "e2e-runs", isPage);

  return {
    unitPage,
    e2ePage,
  };
}

function loadRunDetails(evalType, run) {
  if (!run?.run_id) {
    return;
  }

  const encodedRunId = encodeURIComponent(run.run_id);

  get(`/${evalType}/runs/${encodedRunId}`, `${evalType}-run-detail`, isRun);

  get(
    `/${evalType}/runs/${encodedRunId}/cases?limit=${CASE_LIST_LIMIT}`,
    `${evalType}-run-cases`,
    isPage,
  );
}

function loadNextPage(evalType, page) {
  if (!page?.next_cursor) {
    return;
  }

  const encodedCursor = encodeURIComponent(page.next_cursor);
  get(
    `/${evalType}/runs?limit=${RUN_LIST_LIMIT}&cursor=${encodedCursor}`,
    `${evalType}-runs-next-page`,
    isPage,
  );
}

function loadFilteredRuns(evalType, run) {
  if (!run) {
    return;
  }

  get(
    `/${evalType}/runs?${runFilterQuery(run)}`,
    `${evalType}-runs-filtered`,
    isPage,
  );
}

// --- New read-only coverage; the original helpers and journey above are intact. ---

function query(parameters) {
  const parts = [];
  Object.entries(parameters).forEach(([key, value]) => {
    const values = Array.isArray(value) ? value : [value];
    values.forEach((v) => {
      if (v === undefined || v === null || String(v).trim() === "") return;
      addParameter(parts, key, String(v));
    });
  });
  return parts.length ? `?${parts.join('&')}` : "";
}

function loadUnitFilters(run) {
  if (!run) return;
  const config = run.unit_config || {};
  const category = __ENV.CATEGORY || config.category;
  const stage = __ENV.STAGE || config.stage;
  const target = __ENV.TARGET_ENVIRONMENT || config.target_environment;
  const environment = __ENV.ENVIRONMENT || run.environment;
  const agent = __ENV.AGENT_NAME || config.agent_name;
  const base = "/unit/runs";

  if (category) get(base + query({ limit: RUN_LIST_LIMIT, category }), "unit-runs-category", isPage);
  if (stage) get(base + query({ limit: RUN_LIST_LIMIT, stage }), "unit-runs-stage", isPage);
  if (target) get(base + query({ limit: RUN_LIST_LIMIT, target_environment: target }), "unit-runs-target", isPage);
  if (environment) get(base + query({ limit: RUN_LIST_LIMIT, environment }), "unit-runs-environment", isPage);
  if (agent) get(base + query({ limit: RUN_LIST_LIMIT, agent_name: agent }), "unit-runs-agent", isPage);
  if (category && stage && target) {
    get(base + query({ limit: RUN_LIST_LIMIT, category, stage, target_environment: target }),
      "unit-runs-combined", isPage);
  }
  get(base + query({ limit: RUN_LIST_LIMIT, verdict: "blocked" }), "unit-runs-blocked", isPage);
}

function loadUnitHistory(run) {
  if (!run) return;
  const config = run.unit_config || {};
  const skill = run.skill || config.skill_ids?.[0];
  if (!skill) return;

  const filters = {
    limit: 30,
    environment: run.environment,
    stage: config.stage,
    target_environment: config.target_environment,
    category: config.category,
  };
  const path = `/unit/trends/skills/${encodeURIComponent(skill)}`;
  const page = get(path + query(filters), "unit-skill-history", isPage);
  if (page?.next_cursor) {
    get(path + query({ ...filters, cursor: page.next_cursor }),
      "unit-skill-history-next-page", isPage);
  }

  // Cases only when a known case ID is available.
  const cases = get(`/unit/runs/${encodeURIComponent(run.run_id)}/cases?limit=1`,
    "unit-history-seed-cases", isPage);
  const one = firstRun(cases);
  if (one?.case_id) {
    get(`/unit/trends/cases/${encodeURIComponent(one.case_id)}` +
      query({ ...filters, skill }), "unit-case-history", isPage);
  }
}

function loadE2EHistory(run) {
  if (!run?.stage || !run?.target) return;
  const cases = get(`/e2e/runs/${encodeURIComponent(run.run_id)}/cases?limit=1`,
    "e2e-history-seed-cases", isPage);
  const one = firstRun(cases);
  if (!one?.suite) return;

  const filters = { limit: 30, stage: run.stage, target: run.target };
  get(`/e2e/trends/suites/${encodeURIComponent(one.suite)}` + query(filters),
    "e2e-suite-history", isPage);
  if (one.case_id) {
    get(`/e2e/trends/cases/${encodeURIComponent(one.case_id)}` +
      query({ ...filters, suite: one.suite }), "e2e-case-history", isPage);
  }
}

function loadSkillVersions(run) {
  if (!run) return;
  const cfg = run.unit_config || {};
  const skill = run.skill || cfg.skill_ids?.[0];
  if (!skill) return;
  get(`/unit/skills/${encodeURIComponent(skill)}/versions` +
    query({ environment: run.environment, stage: cfg.stage, target_environment: cfg.target_environment }),
  "unit-skill-versions", Array.isArray);
}

function loadProductUpdates() {
  // No image GET, POST, PATCH, DELETE or upload while GCS IAM is unresolved.
  get("/product-updates?limit=100", "product-updates",
    (value) => value && Array.isArray(value.items));
}

export default function loadEvalApi() {
  const { unitPage, e2ePage } = loadFirstPages();

  const unitRun = firstRun(unitPage);
  const e2eRun = firstRun(e2ePage);

  loadRunDetails("unit", unitRun);
  loadRunDetails("e2e", e2eRun);

  loadNextPage("unit", unitPage);
  loadNextPage("e2e", e2ePage);

  loadFilteredRuns("unit", unitRun);
  loadFilteredRuns("e2e", e2eRun);

  // Rotate new functionality across iterations without changing original calls.
  const rotation = (__VU + __ITER) % 4;
  if (rotation === 0) loadUnitFilters(unitRun);
  else if (rotation === 1) {
    loadUnitHistory(unitRun);
    loadE2EHistory(e2eRun);
  } else if (rotation === 2) loadSkillVersions(unitRun);
  else loadProductUpdates();

  sleep(Number(__ENV.SLEEP_SECONDS || 1));
}
