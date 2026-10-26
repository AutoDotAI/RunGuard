"""Small authenticated deployed-site smoke; never prints credentials or response bodies."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


def request(base: str, path: str, *, token: str | None = None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        base + path,
        headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        if response.status not in (200, 201):
            raise ValueError(f"{path} returned {response.status}")
        return json.load(response)


def main() -> int:
    base = "https://" + os.environ["RUNGUARD_PUBLIC_HOST"]
    email = os.environ["RUNGUARD_SMOKE_EMAIL"]
    password = os.environ["RUNGUARD_SMOKE_PASSWORD"]
    if not email or not password:
        raise ValueError("smoke account credentials are required")
    for _ in range(18):
        try:
            if request(base, "/health") != {"status": "ok"}:
                raise ValueError("readiness response did not match")
            with urllib.request.urlopen(base + "/login", timeout=8) as response:
                if response.status != 200:
                    raise ValueError("web login page did not load")
            credentials = {"email": email, "password": password}
            try:
                auth = request(base, "/api/v1/auth/login", body=credentials)
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise
                auth = request(base, "/api/v1/auth/register", body=credentials)
            token = auth["access_token"]
            if "items" not in request(base, "/api/v1/workers", token=token):
                raise ValueError("worker list was malformed")
            if "total" not in request(base, "/api/v1/dashboard/summary", token=token):
                raise ValueError("dashboard was malformed")
            print("PASS: HTTPS readiness, web page, authenticated worker list and dashboard")
            return 0
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            time.sleep(5)
    print("FAIL: deployed authenticated smoke did not pass", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
