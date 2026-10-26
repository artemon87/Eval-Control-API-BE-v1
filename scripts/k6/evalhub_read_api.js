/**
 * EvalHub read-only k6 workloads. No uploads, mutations, or image downloads.
 * CI options: TEST_CONFIG=.../config.json TEST_NAME=smoke|average_load|stress|spike
 * Host: BASE_URL=https://<host> [EVAL_API_PREFIX=/api/eval]
 * Auth: optional ACCESS_TOKEN; never log tokens.
 *
 * Runs the FE-facing /api/eval proxy by default. Set EVAL_API_PREFIX="" for BE direct.
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";
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
const choose = (array) => array && array.length
  ? array[Math.floor(Math.random() * Math.min(array.length, 20))] : null;
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
  for (const type of ["unit", "e2e"]) {
    const first = list(type);
    const item = choose(first?.items);
    if (item) cases(type, item);
    if (first?.next_cursor) list(type, {}, first.next_cursor, "runs-next-page");
  }
}
function browseFilters() {
  const first = list("unit");
  const item = choose(first?.items);
  const cfg = item?.unit_config || {};
  const category = __ENV.CATEGORY || cfg.category;
  const environment = __ENV.ENVIRONMENT || item?.environment;
  const stage = __ENV.STAGE || item?.stage || cfg.stage;
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
  const skill = cfg.skill || cfg.skill_id || unit?.skill;
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
  const skill = cfg.skill || cfg.skill_id || item?.skill;
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
  // Rotate workloads across VU iterations for varied traffic without unsupported writes.
  const turn = (__ITER + (__VU * 7)) % 10;
  if (turn < 4) browseRuns();
  else if (turn < 7) browseFilters();
  else if (turn < 9) browseHistory();
  else browseUpdates();
  // Additional version read every tenth iteration.
  if ((__ITER + __VU) % 10 === 0) browseVersions();
  pause();
}
