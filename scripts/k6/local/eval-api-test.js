/**
 * Local EvalHub FE proxy k6 test. Copy to tests/load/eval-api-test.js.
 * BASE_URL points at /api/eval, e.g. http://localhost:3000/api/eval.
 * Original smoke/load/spike, original requests and new read-only coverage.
 * No uploads and no GCS images.
 */
import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = (__ENV.BASE_URL || "").replace(/\/$/, "");
if (!baseUrl) throw new Error("BASE_URL is required, for example: http://localhost:3000/api/eval");

const profiles = {
  smoke: [
    { duration: "10s", target: 1 },
    { duration: "20s", target: 2 },
    { duration: "10s", target: 0 },
  ],
  load: [
    { duration: "30s", target: 5 },
    { duration: "1m", target: 15 },
    { duration: "1m", target: 30 },
    { duration: "30s", target: 0 },
  ],
  spike: [
    { duration: "15s", target: 10 },
    { duration: "15s", target: 60 },
    { duration: "30s", target: 60 },
    { duration: "15s", target: 10 },
    { duration: "15s", target: 0 },
  ],
};
const profile = __ENV.PROFILE || "load";
const stages = profiles[profile];
if (!stages) throw new Error('Unknown PROFILE "' + profile + '". Use smoke, load, or spike.');

export const options = {
  stages,
  thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
    http_req_duration: ["p(95)<750", "p(99)<1500"],
    "http_req_duration{name:unit-runs}": ["p(95)<750"],
    "http_req_duration{name:e2e-runs}": ["p(95)<750"],
    "http_req_duration{name:unit-run-detail}": ["p(95)<750"],
    "http_req_duration{name:e2e-run-detail}": ["p(95)<750"],
    "http_req_duration{name:unit-run-cases}": ["p(95)<1200"],
    "http_req_duration{name:e2e-run-cases}": ["p(95)<1200"],
    "http_req_duration{name:unit-runs-next-page}": ["p(95)<1000"],
    "http_req_duration{name:e2e-runs-next-page}": ["p(95)<1000"],
    "http_req_duration{name:unit-runs-filtered}": ["p(95)<1000"],
    "http_req_duration{name:e2e-runs-filtered}": ["p(95)<1000"],
  },
};
const API_BASE = baseUrl;
const RUN_LIST_LIMIT = 50;
const CASE_LIST_LIMIT = 200;
const REQUEST_TIMEOUT = "15s";
const HEADERS = { Accept: "application/json" };
if (__ENV.ACCESS_TOKEN) HEADERS.Authorization = "Bearer " + __ENV.ACCESS_TOKEN;

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
  if (category && stage) {
    get(base + query({ limit: RUN_LIST_LIMIT, category, stage }),
      "unit-runs-category-stage", isPage);
  }
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

function loadE2EStageTarget(run) {
  if (run?.stage && run?.target) {
    get("/e2e/runs" + query({ limit: RUN_LIST_LIMIT, stage: run.stage, target: run.target }),
      "e2e-runs-e2e-stage-target", isPage);
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
  if (rotation === 0) {
    loadUnitFilters(unitRun);
    loadE2EStageTarget(e2eRun);
  }
  else if (rotation === 1) {
    loadUnitHistory(unitRun);
    loadE2EHistory(e2eRun);
  } else if (rotation === 2) loadSkillVersions(unitRun);
  else loadProductUpdates();

  sleep(Number(__ENV.SLEEP_SECONDS || 1));
}
