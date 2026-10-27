import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


WorkerKind = Literal["website_monitor", "url_summarizer"]
RunStatus = Literal["queued", "running", "succeeded", "failed"]
LogLevel = Literal["INFO", "WARN", "ERROR"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value


class AuthUser(StrictModel):
    id: str
    email: str


class TokenResponse(StrictModel):
    access_token: str
    token_type: Literal["bearer"]
    user: AuthUser


class Health(StrictModel):
    status: Literal["ok", "unavailable"]


class AiModel(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=160)


class AiProvider(StrictModel):
    provider_path: Literal["local_demo", "backend_cli", "api"]
    provider: Literal["extractive", "codex", "claude", "litellm"]
    label: str
    available: bool
    unavailable_reason: str | None
    models: list[AiModel]


class AiProviderCatalog(StrictModel):
    providers: list[AiProvider]


class AiSettingsInput(StrictModel):
    provider_path: Literal["local_demo", "backend_cli", "api"]
    provider: Literal["extractive", "codex", "claude", "litellm"]
    model: str = Field(min_length=1, max_length=160)


class AiSettings(AiSettingsInput):
    available: bool


class AiCredentialWrite(StrictModel):
    api_key: SecretStr = Field(min_length=8, max_length=512)


class AiCredentialStatus(StrictModel):
    provider: Literal["openai", "anthropic"]
    configured: bool


class AiCredentialStatuses(StrictModel):
    credentials: list[AiCredentialStatus]


class WorkerCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    kind: WorkerKind
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("name", mode="before")
    @classmethod
    def nonempty_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value


class WorkerUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("name", mode="before")
    @classmethod
    def nonempty_name(cls, value: object) -> object:
        if value is None or not isinstance(value, str):
            return value
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class LastRun(StrictModel):
    id: str
    status: RunStatus
    created_at: str


class Worker(StrictModel):
    id: str
    name: str
    kind: WorkerKind
    url: str
    created_at: str
    updated_at: str
    last_run: LastRun | None = None


class WorkerPage(StrictModel):
    items: list[Worker]
    total: int
    limit: int
    offset: int


class MonitorResult(StrictModel):
    http_status: int
    response_time_ms: int
    healthy: bool


class SummarizerResult(StrictModel):
    source_url: str
    page_title: str | None
    summary: str
    model: str
    input_was_truncated: bool


class RunError(StrictModel):
    code: str
    message: str


class Run(StrictModel):
    id: str
    worker_id: str
    worker_name: str
    kind: WorkerKind
    status: RunStatus
    url: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None
    result: MonitorResult | SummarizerResult | None
    error: RunError | None
    retry_of_run_id: str | None


class RunPage(StrictModel):
    items: list[Run]
    total: int
    limit: int
    offset: int


class EnqueuedRun(StrictModel):
    run_id: str
    worker_id: str
    status: Literal["queued"]
    created_at: str


class RunLog(StrictModel):
    id: int
    timestamp: str
    level: LogLevel
    event: str
    message: str = Field(max_length=512)


class RunLogPage(StrictModel):
    items: list[RunLog]
    next_after_id: int


class DashboardSummary(StrictModel):
    window: Literal["24h"]
    total: int
    succeeded: int
    failed: int
    success_rate: float | None
    avg_duration_ms: int | None
    recent_runs: list[Run]


class Alert(StrictModel):
    id: str
    kind: Literal["repeated_run_failure", "worker_queue_stalled"]
    severity: Literal["warning", "critical"]
    status: Literal["open", "resolved"]
    title: str
    run_id: str
    symptom: str
    fingerprint: str
    created_at: str
    resolved_at: str | None
    trace_id: str | None


class AlertPage(StrictModel):
    items: list[Alert]
    total: int
