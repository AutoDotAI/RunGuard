// Types mirror the RunGuard v1 API contract (spec section 8).
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
  listWorkers(): Promise<Page<Worker>>;
  getWorker(id: string): Promise<Worker>;
  createWorker(input: WorkerInput): Promise<Worker>;
  updateWorker(id: string, input: Pick<WorkerInput, "name" | "url">): Promise<Worker>;
  archiveWorker(id: string): Promise<void>;
  runWorker(id: string): Promise<{ run_id: string; worker_id: string; status: RunStatus; created_at: string }>;
  listRuns(filters?: { worker_id?: string; status?: RunStatus; page?: number; page_size?: number }): Promise<Page<Run>>;
  getRun(id: string): Promise<Run>;
  getRunLogs(id: string, afterId?: number): Promise<{ items: RunLog[]; next_after_id: number }>;
  retryRun(id: string): Promise<{ run_id: string; worker_id: string; status: RunStatus; created_at: string }>;
  getDashboardSummary(): Promise<DashboardSummary>;
}
