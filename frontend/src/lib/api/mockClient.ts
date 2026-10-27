// In-browser mock of the FastAPI backend. Runs are precomputed "scripts" whose
// status and logs are revealed over time, simulating a separate worker process.
import {
  ApiError,
  type ApiClient,
  type LogLevel,
  type Run,
  type RunLog,
  type Worker,
} from "./types";
import { validateName, validateUrl } from "./validation";

interface StoredWorker extends Omit<Worker, "last_run"> {
  owner: string;
  archived: boolean;
}
interface ScriptStep {
  at: number; // ms after created_at
  level: LogLevel;
  event: string;
  message: string;
}
interface StoredRun {
  id: string;
  owner: string;
  worker_id: string;
  worker_name: string;
  kind: Run["kind"];
  url: string;
  created_at: string;
  retry_of_run_id: string | null;
  start_at: number;
  finish_at: number;
  outcome: "succeeded" | "failed";
  result: Run["result"];
  error: Run["error"];
  steps: ScriptStep[];
  log_base: number;
}
interface DB {
  workers: StoredWorker[];
  runs: StoredRun[];
  log_seq: number;
}

const KEY = "runguard.mockdb.v1";
const MAX_WORKERS = 20;
const MAX_ACTIVE = 10;

const load = (): DB => {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return { workers: [], runs: [], log_seq: 0 };
};
const save = (db: DB) => localStorage.setItem(KEY, JSON.stringify(db));
const iso = (ms: number) => new Date(ms).toISOString();
const uid = () => crypto.randomUUID();
const delay = <T,>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 150 + Math.random() * 150));

function statusOf(r: StoredRun, now = Date.now()): Run["status"] {
  const created = Date.parse(r.created_at);
  if (now < created + r.start_at) return "queued";
  if (now < created + r.finish_at) return "running";
  return r.outcome;
}

function toRun(r: StoredRun): Run {
  const created = Date.parse(r.created_at);
  const status = statusOf(r);
  const terminal = status === "succeeded" || status === "failed";
  return {
    id: r.id,
    worker_id: r.worker_id,
    worker_name: r.worker_name,
    kind: r.kind,
    status,
    url: r.url,
    created_at: r.created_at,
    started_at: status === "queued" ? null : iso(created + r.start_at),
    finished_at: terminal ? iso(created + r.finish_at) : null,
    duration_ms: terminal ? r.finish_at - r.start_at : null,
    result: terminal ? r.result : null,
    error: terminal ? r.error : null,
    retry_of_run_id: r.retry_of_run_id,
  };
}

function hostOf(url: string) {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function buildScript(kind: Run["kind"], url: string): Pick<StoredRun, "start_at" | "finish_at" | "outcome" | "result" | "error" | "steps"> {
  const lower = url.toLowerCase();
  const start = 1200 + Math.random() * 800;
  const s = (at: number, level: LogLevel, event: string, message: string): ScriptStep => ({ at, level, event, message });
  const steps: ScriptStep[] = [
    s(0, "INFO", "RUN_QUEUED", "Run queued for execution"),
    s(start, "INFO", "RUN_CLAIMED", "Worker claimed run"),
    s(start + 200, "INFO", "URL_VALIDATING", `Validating URL host ${hostOf(url)}`),
  ];
  if (kind === "website_monitor") {
    const rt = Math.round(180 + Math.random() * 600);
    const fail5xx = /fail|500|503|status\/5/.test(lower);
    const timeout = /timeout|slow/.test(lower);
    steps.push(s(start + 400, "INFO", "HTTP_REQUEST_STARTED", "Checking configured website (timeout 10s)"));
    if (timeout) {
      const end = start + 400 + 10000;
      steps.push(s(end, "ERROR", "HTTP_TIMEOUT", "Request exceeded 10s timeout"));
      steps.push(s(end + 50, "ERROR", "RUN_FAILED", "Run failed: TIMEOUT"));
      return { start_at: start, finish_at: end + 50, outcome: "failed", result: null, error: { code: "TIMEOUT", message: "The website did not respond within 10 seconds." }, steps };
    }
    const code = fail5xx ? 503 : 200;
    const end = start + 400 + rt;
    steps.push(s(end, fail5xx ? "WARN" : "INFO", "HTTP_RESPONSE", `Received HTTP ${code} in ${rt} ms`));
    if (fail5xx) {
      steps.push(s(end + 50, "ERROR", "RUN_FAILED", "Run failed: HTTP_STATUS_ERROR"));
      return { start_at: start, finish_at: end + 50, outcome: "failed", result: { http_status: code, response_time_ms: rt, healthy: false }, error: { code: "HTTP_STATUS_ERROR", message: `Website responded with HTTP ${code}.` }, steps };
    }
    steps.push(s(end + 50, "INFO", "RUN_SUCCEEDED", "Website healthy (status within 200–399)"));
    return { start_at: start, finish_at: end + 50, outcome: "succeeded", result: { http_status: code, response_time_ms: rt, healthy: true }, error: null, steps };
  }
  // url_summarizer
  let t = start + 400;
  steps.push(s(t, "INFO", "FETCH_STARTED", "Fetching page (bounded, max 2 MB)"));
  t += 700;
  if (/\.pdf|\.png|\.jpg|\.zip/.test(lower)) {
    steps.push(s(t, "ERROR", "UNSUPPORTED_CONTENT", "Content-Type is not text/html"));
    steps.push(s(t + 50, "ERROR", "RUN_FAILED", "Run failed: UNSUPPORTED_CONTENT"));
    return { start_at: start, finish_at: t + 50, outcome: "failed", result: null, error: { code: "UNSUPPORTED_CONTENT", message: "The page is not an HTML document." }, steps };
  }
  steps.push(s(t, "INFO", "FETCH_COMPLETED", "Fetched HTML document"));
  t += 300;
  steps.push(s(t, "INFO", "EXTRACTING", "Extracted readable text (8,412 chars)"));
  t += 200;
  steps.push(s(t, "INFO", "LLM_REQUEST_STARTED", "Requesting summary from configured model"));
  t += 2500;
  if (/fail|llm-error/.test(lower)) {
    steps.push(s(t, "ERROR", "LLM_ERROR", "Provider returned an error; no automatic retry"));
    steps.push(s(t + 50, "ERROR", "RUN_FAILED", "Run failed: LLM_PROVIDER_ERROR"));
    return { start_at: start, finish_at: t + 50, outcome: "failed", result: null, error: { code: "LLM_PROVIDER_ERROR", message: "The language model provider failed to respond." }, steps };
  }
  steps.push(s(t, "INFO", "SUMMARY_SAVED", "Saved summary"));
  steps.push(s(t + 50, "INFO", "RUN_SUCCEEDED", "Summarization completed"));
  const host = hostOf(url);
  return {
    start_at: start,
    finish_at: t + 50,
    outcome: "succeeded",
    result: {
      source_url: url,
      page_title: `${host} — page`,
      summary: `## Overview of ${host}\n- The page introduces its main subject and intended audience.\n- It outlines key features and how they are used in practice.\n- Supporting details and examples clarify the core ideas.\n- It points readers to further resources on the same site.`,
      model: "mock/demo-model",
      input_was_truncated: false,
    },
    error: null,
    steps,
  };
}

export function createMockClient(getUserId: () => string | null): ApiClient {
  const owner = () => {
    const id = getUserId();
    if (!id) throw new ApiError(401, "UNAUTHORIZED", "Sign in required.");
    return id;
  };
  const findWorker = (db: DB, id: string, me: string) => {
    const w = db.workers.find((x) => x.id === id && x.owner === me);
    if (!w) throw new ApiError(404, "NOT_FOUND", "Worker not found.");
    return w;
  };
  const findRun = (db: DB, id: string, me: string) => {
    const r = db.runs.find((x) => x.id === id && x.owner === me);
    if (!r) throw new ApiError(404, "NOT_FOUND", "Run not found.");
    return r;
  };
  const publicWorker = (db: DB, w: StoredWorker): Worker => {
    const last = db.runs.filter((r) => r.worker_id === w.id).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
    const { owner: _o, archived: _a, ...rest } = w;
    return { ...rest, last_run: last ? { id: last.id, status: statusOf(last), created_at: last.created_at } : null };
  };
  const enqueue = (db: DB, me: string, w: StoredWorker, url: string, retryOf: string | null) => {
    const active = db.runs.filter((r) => r.owner === me && ["queued", "running"].includes(statusOf(r)));
    const existing = active.find((r) => r.worker_id === w.id);
    if (existing) throw new ApiError(409, "RUN_ALREADY_ACTIVE", "This worker already has an active run.", { run_id: existing.id });
    if (active.length >= MAX_ACTIVE) throw new ApiError(429, "TOO_MANY_ACTIVE_RUNS", "You have too many queued or running runs.");
    const script = buildScript(w.kind, url);
    const run: StoredRun = {
      id: uid(),
      owner: me,
      worker_id: w.id,
      worker_name: w.name,
      kind: w.kind,
      url,
      created_at: iso(Date.now()),
      retry_of_run_id: retryOf,
      log_base: db.log_seq,
      ...script,
    };
    db.log_seq += script.steps.length;
    db.runs.push(run);
    save(db);
    return { run_id: run.id, worker_id: w.id, status: "queued" as const, created_at: run.created_at };
  };

  return {
    async getAiProviders() {
      owner();
      // The demo cannot attest to a host CLI worker or server-side API key.
      return delay({ providers: [] });
    },
    async getAiSettings() {
      owner();
      return delay({ provider_path: "backend_cli", provider: "codex", model: "default", available: false });
    },
    async updateAiSettings() {
      owner();
      throw new ApiError(409, "PROVIDER_UNAVAILABLE", "No AI provider is connected in demo mode.");
    },
    async getAiCredentials() {
      owner();
      return delay({ credentials: [
        { provider: "openai" as const, configured: false },
        { provider: "anthropic" as const, configured: false },
      ] });
    },
    async upsertAiCredential() {
      owner();
      throw new ApiError(409, "PROVIDER_UNAVAILABLE", "API keys cannot be configured in demo mode.");
    },
    async deleteAiCredential() {
      owner();
      throw new ApiError(409, "PROVIDER_UNAVAILABLE", "API keys cannot be configured in demo mode.");
    },
    async listWorkers(pagination: { limit?: number; offset?: number } = {}) {
      const me = owner();
      const db = load();
      const items = db.workers.filter((w) => w.owner === me && !w.archived).sort((a, b) => b.created_at.localeCompare(a.created_at)).map((w) => publicWorker(db, w));
      const limit = pagination.limit ?? 20;
      const offset = pagination.offset ?? 0;
      return delay({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
    },
    async getWorker(id) {
      const me = owner();
      const db = load();
      const w = findWorker(db, id, me);
      if (w.archived) throw new ApiError(404, "NOT_FOUND", "Worker not found.");
      return delay(publicWorker(db, w));
    },
    async createWorker(input) {
      const me = owner();
      const db = load();
      const nameErr = validateName(input.name);
      const urlErr = validateUrl(input.url);
      if (nameErr || urlErr) throw new ApiError(422, "VALIDATION_ERROR", nameErr ?? urlErr!, { name: nameErr, url: urlErr });
      if (db.workers.filter((w) => w.owner === me && !w.archived).length >= MAX_WORKERS) throw new ApiError(409, "WORKER_LIMIT_REACHED", `You can save at most ${MAX_WORKERS} workers.`);
      const now = iso(Date.now());
      const w: StoredWorker = { id: uid(), owner: me, archived: false, name: input.name.trim(), kind: input.kind, url: input.url.trim(), created_at: now, updated_at: now };
      db.workers.push(w);
      save(db);
      return delay(publicWorker(db, w));
    },
    async updateWorker(id, input) {
      const me = owner();
      const db = load();
      const w = findWorker(db, id, me);
      if (w.archived) throw new ApiError(409, "WORKER_ARCHIVED", "Archived workers cannot be edited.");
      const nameErr = validateName(input.name);
      const urlErr = validateUrl(input.url);
      if (nameErr || urlErr) throw new ApiError(422, "VALIDATION_ERROR", nameErr ?? urlErr!, { name: nameErr, url: urlErr });
      w.name = input.name.trim();
      w.url = input.url.trim();
      w.updated_at = iso(Date.now());
      save(db);
      return delay(publicWorker(db, w));
    },
    async archiveWorker(id) {
      const me = owner();
      const db = load();
      findWorker(db, id, me).archived = true;
      save(db);
      return delay(undefined);
    },
    async runWorker(id) {
      const me = owner();
      const db = load();
      const w = findWorker(db, id, me);
      if (w.archived) throw new ApiError(409, "WORKER_ARCHIVED", "Archived workers cannot be run.");
      return delay(enqueue(db, me, w, w.url, null));
    },
    async listRuns(filters = {}) {
      const me = owner();
      const db = load();
      let runs = db.runs.filter((r) => r.owner === me).map(toRun);
      if (filters.worker_id) runs = runs.filter((r) => r.worker_id === filters.worker_id);
      if (filters.status) runs = runs.filter((r) => r.status === filters.status);
      runs.sort((a, b) => b.created_at.localeCompare(a.created_at));
      const size = filters.page_size ?? 20;
      const page = filters.page ?? 1;
      return delay({ items: runs.slice((page - 1) * size, page * size), total: runs.length, limit: size, offset: (page - 1) * size });
    },
    async getRun(id) {
      const me = owner();
      return delay(toRun(findRun(load(), id, me)));
    },
    async getRunLogs(id, afterId = 0) {
      const me = owner();
      const r = findRun(load(), id, me);
      const elapsed = Date.now() - Date.parse(r.created_at);
      const created = Date.parse(r.created_at);
      const items: RunLog[] = r.steps
        .map((st, i) => ({ st, id: r.log_base + i + 1 }))
        .filter(({ st, id }) => st.at <= elapsed && id > afterId)
        .map(({ st, id }) => ({ id, timestamp: iso(created + st.at), level: st.level, event: st.event, message: st.message }));
      return delay({ items, next_after_id: items.length ? items[items.length - 1]!.id : afterId });
    },
    async retryRun(id) {
      const me = owner();
      const db = load();
      const r = findRun(db, id, me);
      if (statusOf(r) !== "failed") throw new ApiError(409, "INVALID_RETRY_STATE", "Only failed runs can be retried.");
      const w = findWorker(db, r.worker_id, me);
      if (w.archived) throw new ApiError(409, "WORKER_ARCHIVED", "The worker for this run is archived.");
      // Retry uses the original run's snapshotted URL, not later edits.
      return delay(enqueue(db, me, w, r.url, r.id));
    },
    async getDashboardSummary() {
      const me = owner();
      const db = load();
      const since = Date.now() - 24 * 3600 * 1000;
      const runs = db.runs.filter((r) => r.owner === me).map(toRun).sort((a, b) => b.created_at.localeCompare(a.created_at));
      const windowRuns = runs.filter((r) => Date.parse(r.created_at) >= since);
      const done = windowRuns.filter((r) => r.status === "succeeded" || r.status === "failed");
      const succeeded = done.filter((r) => r.status === "succeeded").length;
      return delay({
        window: "24h" as const,
        total: windowRuns.length,
        succeeded,
        failed: done.length - succeeded,
        success_rate: done.length ? succeeded / done.length : null,
        avg_duration_ms: done.length ? Math.round(done.reduce((a, r) => a + (r.duration_ms ?? 0), 0) / done.length) : null,
        recent_runs: runs.slice(0, 10),
      });
    },
    async getAlerts() {
      owner();
      return delay({ items: [], total: 0 });
    },
  };
}
