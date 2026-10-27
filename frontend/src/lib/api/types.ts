// Types mirror the RunGuard v1 API contract (spec section 8).
export interface Health {
  status: "ok" | "unavailable";
}

export type WorkerKind = "website_monitor" | "url_summarizer";
export type RunStatus = "queued" | "running" | "succeeded" | "failed";
export type LogLevel = "INFO" | "WARN" | "ERROR";

export interface Worker {
  id: string;
  name: string;
  kind: WorkerKind;
  url: string;
  created_at: string;
  updated_at: string;
  last_run?: { id: string; status: RunStatus; created_at: string } | null;
}

export interface MonitorResult {
  http_status: number;
  response_time_ms: number;
  healthy: boolean;
}
export interface SummarizerResult {
  source_url: string;
  page_title: string | null;
  summary: string;
  model: string;
  input_was_truncated: boolean;
}

export interface RunError {
  code: string;
  message: string;
}

export interface Run {
  id: string;
  worker_id: string;
  worker_name: string;
  kind: WorkerKind;
  status: RunStatus;
  url: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  result: MonitorResult | SummarizerResult | null;
  error: RunError | null;
  retry_of_run_id: string | null;
}

export interface RunLog {
  id: number;
  timestamp: string;
  level: LogLevel;
  event: string;
  message: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Credentials {
  email: string;
  password: string;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: "bearer";
  user: { id: string; email: string };
}

export interface ErrorResponse {
  error: { code: string; message: string; details?: Record<string, unknown> };
  request_id: string;
}

export interface EnqueuedRun {
  run_id: string;
  worker_id: string;
  status: "queued";
  created_at: string;
}

export interface RunLogPage {
  items: RunLog[];
  next_after_id: number;
}

export interface DashboardSummary {
  window: "24h";
  total: number;
  succeeded: number;
  failed: number;
  success_rate: number | null;
  avg_duration_ms: number | null;
  recent_runs: Run[];
}

export interface Alert {
  id: string;
  kind: "repeated_run_failure" | "worker_queue_stalled";
  severity: "warning" | "critical";
  status: "open" | "resolved";
  title: string;
  run_id: string;
  symptom: string;
  fingerprint: string;
  created_at: string;
  resolved_at: string | null;
  trace_id: string | null;
}

export interface AlertPage {
  items: Alert[];
  total: number;
}

export type AiProviderPath = "local_demo" | "backend_cli" | "api";
export type AiProviderId = "extractive" | "codex" | "claude" | "litellm";

export interface AiModel {
  id: string;
  label: string;
}

export interface AiProvider {
  provider_path: AiProviderPath;
  provider: AiProviderId;
  label: string;
  available: boolean;
  unavailable_reason: string | null;
  models: AiModel[];
}

export interface AiProviderCatalog {
  providers: AiProvider[];
}

export interface AiSettingsInput {
  provider_path: AiProviderPath;
  provider: AiProviderId;
  model: string;
}

export interface AiSettings {
  provider_path: AiProviderPath;
  provider: AiProviderId;
  model: string;
  available: boolean;
}

export interface WorkerInput {
  name: string;
  kind: WorkerKind;
  url: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

export interface ApiClient {
  getAiProviders(): Promise<AiProviderCatalog>;
  getAiSettings(): Promise<AiSettings>;
  updateAiSettings(input: AiSettingsInput): Promise<AiSettings>;
  listWorkers(pagination?: { limit?: number; offset?: number }): Promise<Page<Worker>>;
  getWorker(id: string): Promise<Worker>;
  createWorker(input: WorkerInput): Promise<Worker>;
  updateWorker(id: string, input: Pick<WorkerInput, "name" | "url">): Promise<Worker>;
  archiveWorker(id: string): Promise<void>;
  runWorker(id: string): Promise<EnqueuedRun>;
  listRuns(filters?: { worker_id?: string; status?: RunStatus; page?: number; page_size?: number }): Promise<Page<Run>>;
  getRun(id: string): Promise<Run>;
  getRunLogs(id: string, afterId?: number): Promise<RunLogPage>;
  retryRun(id: string): Promise<EnqueuedRun>;
  getDashboardSummary(): Promise<DashboardSummary>;
  getAlerts(): Promise<AlertPage>;
}
