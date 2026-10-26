"""Keep the reviewable API contract, FastAPI routes, and shared UI types aligned."""

from pathlib import Path
import re

import yaml
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "post", "patch", "delete"}
# The approved P0 surface in _docs/old/SPEC.md section 8. P1 /alerts is deferred.
OPERATIONS = {
    ("get", "/health"): ("200", "Health", None),
    ("post", "/api/v1/auth/register"): ("201", "TokenResponse", "Credentials"),
    ("post", "/api/v1/auth/login"): ("200", "TokenResponse", "Credentials"),
    ("get", "/api/v1/workers"): ("200", "WorkerPage", None),
    ("post", "/api/v1/workers"): ("201", "Worker", "WorkerCreate"),
    ("get", "/api/v1/workers/{worker_id}"): ("200", "Worker", None),
    ("patch", "/api/v1/workers/{worker_id}"): ("200", "Worker", "WorkerUpdate"),
    ("delete", "/api/v1/workers/{worker_id}"): ("204", None, None),
    ("post", "/api/v1/workers/{worker_id}/runs"): ("202", "EnqueuedRun", None),
    ("get", "/api/v1/runs"): ("200", "RunPage", None),
    ("get", "/api/v1/runs/{run_id}"): ("200", "Run", None),
    ("get", "/api/v1/runs/{run_id}/logs"): ("200", "RunLogPage", None),
    ("post", "/api/v1/runs/{run_id}/retry"): ("202", "EnqueuedRun", None),
    ("get", "/api/v1/dashboard/summary"): ("200", "DashboardSummary", None),
}
PUBLIC = {("get", "/health"), ("post", "/api/v1/auth/register"), ("post", "/api/v1/auth/login")}
QUERY = {
    ("get", "/api/v1/workers"): {"limit", "offset"},
    ("get", "/api/v1/runs"): {"worker_id", "status", "page", "page_size"},
    ("get", "/api/v1/runs/{run_id}/logs"): {"after_id"},
    ("get", "/api/v1/dashboard/summary"): {"window"},
}
SHARED = {
    "Health": "Health", "TokenResponse": "AuthTokenResponse", "Worker": "Worker",
    "WorkerPage": "Page", "MonitorResult": "MonitorResult",
    "SummarizerResult": "SummarizerResult", "Run": "Run", "RunPage": "Page",
    "EnqueuedRun": "EnqueuedRun", "RunLog": "RunLog", "RunLogPage": "RunLogPage",
    "DashboardSummary": "DashboardSummary",
}


def contract():
    document = yaml.safe_load((ROOT / "openapi.yaml").read_text())
    assert document["openapi"].startswith("3.1.")
    return document


def resolve(document, node):
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        assert ref.startswith("#/"), ref
        node = document
        for segment in ref[2:].split("/"):
            node = node[segment]
    return node


def all_refs_resolve(document, node):
    if isinstance(node, dict):
        if "$ref" in node:
            resolve(document, node)
        for child in node.values():
            all_refs_resolve(document, child)
    elif isinstance(node, list):
        for child in node:
            all_refs_resolve(document, child)


def operations(document):
    return {(method, path): spec for path, path_item in document["paths"].items()
            for method, spec in path_item.items() if method in METHODS}


def schema_of_response(document, operation, status):
    response = resolve(document, operation["responses"][status])
    content = response.get("content", {})
    return content.get("application/json", {}).get("schema")


def query_names(document, path, operation):
    parameters = document["paths"][path].get("parameters", []) + operation.get("parameters", [])
    return {resolve(document, parameter)["name"] for parameter in parameters
            if resolve(document, parameter)["in"] == "query"}


def type_script_required(interface: str) -> set[str]:
    text = (ROOT / "frontend/src/lib/api/types.ts").read_text()
    match = re.search(rf"export interface {interface}(?:<[^>]+>)? \{{(.*?)^\}}", text, re.M | re.S)
    assert match, interface
    return {name for name in re.findall(r"^\s+([a-z_][a-z_0-9]*):", match.group(1), re.M)}


def test_openapi_has_exact_p0_operations_resolved_references_and_explicit_responses():
    document = contract()
    validate(document)
    all_refs_resolve(document, document)
    found = operations(document)
    assert set(found) == set(OPERATIONS)
    assert "/api/v1/alerts" not in document["paths"]
    assert document["components"]["securitySchemes"]["localBearer"]["scheme"] == "bearer"
    enqueue = found[("post", "/api/v1/workers/{worker_id}/runs")]
    assert {"400", "401", "404", "409", "429"} <= set(enqueue["responses"])
    for key, (success, response_schema, request_schema) in OPERATIONS.items():
        operation = found[key]
        assert operation.get("operationId"), key
        assert success in operation["responses"], key
        actual_response = schema_of_response(document, operation, success)
        if response_schema is None:
            assert actual_response is None and success == "204", key
        else:
            assert operation["responses"][success].get("content", {}).get("application/json"), key
            assert actual_response == {"$ref": f"#/components/schemas/{response_schema}"}, key
        if request_schema is None:
            assert "requestBody" not in operation, key
        else:
            body = operation["requestBody"]
            assert body["required"] is True, key
            assert body["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{request_schema}"}, key
        if key in PUBLIC:
            assert operation.get("security", document.get("security")) == [], key
        else:
            assert operation.get("security", document.get("security")) == [{"localBearer": []}], key
            assert "401" in operation["responses"], key
        if key != ("get", "/health"):
            assert "500" in operation["responses"], key
        for status, response in operation["responses"].items():
            if status not in (success, "503"):
                assert schema_of_response(document, operation, status) == {"$ref": "#/components/schemas/ErrorResponse"}, (key, status)
        assert query_names(document, key[1], operation) == QUERY.get(key, set()), key


def test_fastapi_generated_openapi_has_matching_operations_auth_and_response_fields(tmp_path):
    expected = contract()
    with TestClient(create_app(secret="contract-test-secret-at-least-32-bytes",
                               database_url=f"sqlite:///{tmp_path / 'contract.db'}")) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    generated = response.json()
    actual = operations(generated)
    assert set(actual) == set(OPERATIONS)
    for key, (success, _response_schema, request_schema) in OPERATIONS.items():
        documented = operations(expected)[key]
        implementation = actual[key]
        assert success in implementation["responses"], key
        assert ("requestBody" in implementation) is (request_schema is not None), key
        assert bool(implementation.get("security")) is (key not in PUBLIC), key
        assert query_names(generated, key[1], implementation) == QUERY.get(key, set()), key
        if key not in PUBLIC:
            assert implementation["security"] == [{"localBearer": []}], key
        if success != "204":
            documented_name = schema_of_response(expected, documented, success)["$ref"].split("/")[-1]
            generated_name = schema_of_response(generated, implementation, success)["$ref"].split("/")[-1]
            assert documented_name == generated_name, key
    for schema in SHARED:
        static_required = set(expected["components"]["schemas"][schema].get("required", []))
        generated_required = set(generated["components"]["schemas"][schema].get("required", []))
        assert static_required == generated_required, schema
    assert set(generated["components"]["schemas"]["AuthUser"]["required"]) == {"id", "email"}


def test_frontend_shared_types_and_error_envelope_match_required_fields(tmp_path):
    document = contract()
    for schema, interface in SHARED.items():
        required = set(document["components"]["schemas"][schema].get("required", []))
        assert required == type_script_required(interface), (schema, interface)
    assert set(document["components"]["schemas"]["TokenResponse"]["properties"]["user"]["required"]) == {"id", "email"}
    assert set(document["components"]["schemas"]["ErrorResponse"]["required"]) == {"error", "request_id"}
    assert set(document["components"]["schemas"]["Error"]["required"]) == {"code", "message"}
    assert "details" in document["components"]["schemas"]["Error"]["properties"]
    assert "run_id" in document["components"]["schemas"]["Error"]["properties"]["details"]["description"]
    assert type_script_required("ErrorResponse") == {"error", "request_id"}
    assert set(document["components"]["schemas"]["RunStatus"]["enum"]) == {"queued", "running", "succeeded", "failed"}
    assert set(document["components"]["schemas"]["WorkerKind"]["enum"]) == {"website_monitor", "url_summarizer"}
    assert set(document["components"]["schemas"]["LogLevel"]["enum"]) == {"INFO", "WARN", "ERROR"}
    with TestClient(create_app(secret="contract-test-secret-at-least-32-bytes",
                               database_url=f"sqlite:///{tmp_path / 'contract.db'}")) as client:
        unauthorized = client.get("/api/v1/workers")
    assert unauthorized.status_code == 401
    assert set(unauthorized.json()) == {"error", "request_id"}
    assert set(unauthorized.json()["error"]) >= {"code", "message"}
