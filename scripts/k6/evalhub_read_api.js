/**
 * k6 load test for EvalHub eval API.
 * Preserves original UI journey and endpoint names for CI continuity:
 * initial Unit/E2E lists, newest run details/cases, next pages, Git filters.
 * Extends coverage for category/stage/target, history and What's New JSON.
 * Read-only; intentionally does NOT request GCS images or uploads.
 *
 * CI config: TEST_CONFIG=./scripts/k6/config.json TEST_NAME=smoke|average_load|stress|spike
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter } from "k6/metrics";

const optionsFile = __ENV.TEST_CONFIG;
const config = optionsFile ? JSON.parse(open(optionsFile)) : {};
const selection = __ENV.TEST_NAME || "smoke";
export const options = config.tests?.[selection] || {
  vus: 1, duration: "1m", thresholds: {
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
};

const host = (__ENV.BASE_URL || "http://localhost:3000").replace(/\/+$/, "");
const prefix = __ENV.EVAL_API_PREFIX !== undefined
  ? __ENV.EVAL_API_PREFIX
  : (host.endsWith("/api/eval") ? "" : "/api/eval");
const api = host + prefix;
const limit = 50;
const caseLimit = 200;
const badPayloads = new Counter("evalhub_bad_payloads");

function params(values) {
  const result = [];
  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined || value === "") return;
    for (const item of (Array.isArray(value) ? value : [value])) {
      if (item === null || item === undefined || item === "") continue;
      result.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(item)));
    }
  });
  return result.length ? "?" + result.join("&") : "";
}

function request(path, name, validator = () => true) {
  const headers = { Accept: "application/json" };
  if (__ENV.ACCESS_TOKEN) headers.Authorization = "Bearer " + __ENV.ACCESS_TOKEN;
  const result = http.get(api + path, {
    tags: { name },
    headers,
    timeout: "60s",
  });
  let body = null;
  try { body = result.json(); } catch (_) { /* handled by check */ }
  const valid = body !== null && validator(body);
  check(result, {
    [name + " status 200"]: (r) => r.status === 200,
    [name + " JSON shape"]: () => valid,
  });
  if (result.status === 200 && !valid) badPayloads.add(1, { name });
  return result.status === 200 && valid ? body : null;
}

const page = (v) => v && typeof v === "object" &&
  Array.isArray(v.items) && Object.prototype.hasOwnProperty.call(v, "next_cursor");
const run = (v) => v && typeof v === "object" && typeof v.run_id === "string";
const updates = (v) => v && Array.isArray(v.items);
const choose = (array) => array && array.length ? array[0] : null;
const pause = () => sleep(Number(__ENV.SLEEP_SECONDS || "1"));
const encoded = (s) => encodeURIComponent(s);

function list(type, filters = {}, cursor, label = "runs") {
  return request("/" + type + "/runs" + params({
    limit, ...filters, cursor,
  }), type + "-" + label, page);
}
function cases(type, item) {
  if (!run(item)) return;
  const path = "/" + type + "/runs/" + encoded(item.run_id);
  request(path, type + "-run-detail", run);
  const first = request(path + "/cases" + params({ limit: caseLimit }), type + "-run-cases", page);
  if (first?.next_cursor && __ENV.PAGE_CASES === "1") {
    request(path + "/cases" + params({ limit: caseLimit, cursor: first.next_cursor }),
      type + "-cases-next-page", page);
  }
}
function browseRuns() {
  // Original default journey: list both types, open first run of each type,
  // fetch cases, page forward once, then query Git/repository filters.
  const unit = list("unit");
  const e2e = list("e2e");
  const unitRun = choose(unit?.items);
  const e2eRun = choose(e2e?.items);
  if (unitRun) cases("unit", unitRun);
  if (e2eRun) cases("e2e", e2eRun);
  if (unit?.next_cursor) list("unit", {}, unit.next_cursor, "runs-next-page");
  if (e2e?.next_cursor) list("e2e", {}, e2e.next_cursor, "runs-next-page");
  const gitFilters = (item) => ({
    github_repository: __ENV.GITHUB_REPOSITORY || item?.github_repository,
    git_ref: __ENV.GIT_REF || item?.git_ref,
    git_sha: __ENV.GIT_SHA || item?.git_sha,
  });
  if (unitRun) list("unit", gitFilters(unitRun), null, "runs-filtered");
  if (e2eRun) list("e2e", gitFilters(e2eRun), null, "runs-filtered");
}
function browseFilters() {
  const first = list("unit");
  const item = choose(first?.items);
  const cfg = item?.unit_config || {};
  const category = __ENV.CATEGORY || cfg.category;
  const environment = __ENV.ENVIRONMENT || item?.environment;
  const stage = __ENV.STAGE || cfg.stage || item?.stage;
  const target = __ENV.TARGET_ENVIRONMENT || cfg.target_environment;
  const agent = __ENV.AGENT_NAME || cfg.agent_name;
  // Filter combinations reflect the current FE, including repeated stage params.
  if (category) list("unit", { category }, null, "runs-category");
  if (stage) list("unit", { stage: [stage] }, null, "runs-stage");
  if (category && stage) list("unit", { category, stage: [stage] }, null, "runs-category-stage");
  if (target) list("unit", { target_environment: target }, null, "runs-target");
  if (environment) list("unit", { environment }, null, "runs-environment");
  if (agent) list("unit", { agent_name: agent }, null, "runs-agent");
  if (category && stage && target) {
    list("unit", { category, stage: [stage], target_environment: target }, null, "runs-combined");
  }
  if (first?.items?.length) list("unit", { verdict: "blocked" }, null, "runs-blocked");

  const e2e = list("e2e");
  const e2eItem = choose(e2e?.items);
  if (e2eItem?.stage && e2eItem?.target) {
    list("e2e", { stage: e2eItem.stage, target: e2eItem.target },
      null, "runs-e2e-stage-target");
  }
}
function browseHistory() {
  const unitPage = list("unit", {}, null, "history-seed-unit");
  const unit = choose(unitPage?.items);
  const cfg = unit?.unit_config || {};
  const skill = unit?.skill || cfg.skill_ids?.[0];
  if (skill) {
    const query = {
      limit: 30,
      environment: unit?.environment,
      target_environment: cfg.target_environment,
      category: cfg.category,
    };
    const path = "/unit/trends/skills/" + encoded(skill);
    const first = request(path + params(query), "unit-skill-history", page);
    if (first?.next_cursor) {
      request(path + params({ ...query, cursor: first.next_cursor }),
        "unit-skill-history-next-page", page);
    }
  }
  const e2ePage = list("e2e", {}, null, "history-seed-e2e");
  const e2e = choose(e2ePage?.items);
  if (e2e?.stage && e2e?.target) {
    // Suite name is resolved from E2E case metadata.
    const casePage = request("/e2e/runs/" + encoded(e2e.run_id) +
      "/cases" + params({ limit: caseLimit }), "e2e-history-seed-cases", page);
    const c = choose(casePage?.items);
    if (c?.suite) {
      request("/e2e/trends/suites/" + encoded(c.suite) +
        params({ stage: e2e.stage, target: e2e.target, limit: 30 }),
        "e2e-suite-history", page);
      if (c.case_id) request("/e2e/trends/cases/" + encoded(c.case_id) +
        params({ stage: e2e.stage, target: e2e.target, suite: c.suite, limit: 30 }),
        "e2e-case-history", page);
    }
  }
}
function browseUpdates() {
  // No /{id}/image until GCS bucket IAM is fixed; no POST/PATCH/DELETE.
  request("/product-updates" + params({ limit: 100 }), "product-updates", updates);
}
function browseVersions() {
  const unitPage = list("unit", {}, null, "versions-seed");
  const item = choose(unitPage?.items);
  const cfg = item?.unit_config || {};
  const skill = item?.skill || cfg.skill_ids?.[0];
  if (!skill) return;
  const q = params({
    environment: item.environment,
    stage: item.stage,
    target_environment: cfg.target_environment,
  });
  const path = "/unit/skills/" + encoded(skill);
  request(path + "/versions" + q, "unit-skill-versions", Array.isArray);
  // Only request comparison if versions are available to avoid expected 404.
}
export default function () {
  // Every VU repeats the original journey. Additional reads rotate per iteration
  // to limit amplification while still exercising recently added FE paths.
  browseRuns();
  const turn = (__ITER + __VU) % 4;
  if (turn === 0) browseFilters();
  else if (turn === 1) browseHistory();
  else if (turn === 2) browseUpdates();
  else browseVersions();
  pause();
}
