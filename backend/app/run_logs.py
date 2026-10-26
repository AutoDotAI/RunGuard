"""Single safe boundary for persisted run log events.

Worker adapters may handle untrusted pages, URLs and exceptions. Persist only
known event codes and generated messages; never pass their text to SQLite.
"""

import re

from .db import RunLogRecord


MAX_MESSAGE_LENGTH = 512

MESSAGES = {
    "RUN_QUEUED": "Run queued for execution",
    "RUN_CLAIMED": "Run claimed by worker",
    "RUN_STARTED": "Run started",
    "URL_VALIDATION_STARTED": "Validating configured URL",
    "HTTP_REQUEST_STARTED": "Requesting configured website",
    "HTTP_REQUEST_FAILED": "Website request failed",
    "HTTP_RESPONSE_RECEIVED": "HTTP response received",
    "TEXT_EXTRACTION_STARTED": "Extracting readable page text",
    "TEXT_EXTRACTION_FAILED": "Readable page text could not be extracted",
    "TEXT_EXTRACTED": "Readable page text extracted",
    "INPUT_TRUNCATED": "Readable page text truncated to input limit",
    "LLM_REQUEST_STARTED": "Requesting summary",
    "LLM_REQUEST_FAILED": "Summarization provider failed",
    "LLM_RESPONSE_RECEIVED": "Summary received",
    "SUMMARY_SAVED": "Summary saved",
    "RUN_SUCCEEDED": "Run succeeded",
    "RUN_FAILED": "Run failed",
    "WORKER_INTERRUPTED": "Worker stopped before the run completed",
    "DEMO_WORKER_PAUSED": "Local demo worker paused; queued run is waiting",
    "DEMO_WORKER_RESUMED": "Local demo worker resumed for this run",
    "HANDLER_UNAVAILABLE": "No handler is available for this worker type",
    "HANDLER_ERROR": "Worker handler failed",
    "URL_INVALID": "A valid public HTTP(S) URL is required",
    "DNS_ERROR": "Destination resolution failed",
    "DESTINATION_BLOCKED": "Destination address is not public",
    "FETCH_TIMEOUT": "Destination request timed out",
    "FETCH_ERROR": "Destination request failed",
    "RESPONSE_TOO_LARGE": "Response exceeds size limit",
    "HTTP_STATUS_ERROR": "HTTP request returned an error status",
    "UNSUPPORTED_CONTENT_TYPE": "HTML content is required",
    "UNSUPPORTED_CHARSET": "Page character encoding is unsupported",
    "INSUFFICIENT_CONTENT": "Page has insufficient readable text",
    "EXTRACTION_ERROR": "Readable page text could not be extracted",
    "LLM_ERROR": "Summarization provider failed",
}


def safe_event(event: str, message: str = "") -> tuple[str, str]:
    """Resolve an event to a stable code and a message built from safe values."""
    if event not in MESSAGES:
        return "HANDLER_ERROR", MESSAGES["HANDLER_ERROR"]
    if event in {"HTTP_RESPONSE_RECEIVED", "HTTP_STATUS_ERROR"}:
        match = re.fullmatch(r"Received HTTP ([1-5][0-9]{2})", message)
        if match:
            return event, message
    return event, MESSAGES[event]


def new_run_log(run_id: str, owner_user_id: str, timestamp: str,
                level: str, event: str, message: str = "") -> RunLogRecord:
    code, safe_message = safe_event(event, message)
    safe_level = level if level in {"INFO", "WARN", "ERROR"} else "ERROR"
    return RunLogRecord(run_id=run_id, owner_user_id=owner_user_id,
                        timestamp=timestamp, level=safe_level, event=code,
                        message=safe_message[:MAX_MESSAGE_LENGTH])
