"""Validate immutable release identifiers and development promotion evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[1-9][0-9]*\Z")
IMAGES = {
    "api": "ghcr.io/autodotai/runguard-api",
    "web": "ghcr.io/autodotai/runguard-web",
    "egress": "ghcr.io/autodotai/runguard-egress",
}


def release(commit: str, api: str, web: str, egress: str) -> dict[str, str]:
    if not SHA.fullmatch(commit):
        raise ValueError("commit must be a 40-character lowercase hex SHA")
    values = {"api": api, "web": web, "egress": egress}
    for kind, digest in values.items():
        if not DIGEST.fullmatch(digest):
            raise ValueError(f"{kind} must be a sha256 digest")
    return {"commit": commit, **values}


def check_promotion(evidence: dict, source_run_id: str, requested: dict[str, str]) -> None:
    if not RUN_ID.fullmatch(source_run_id):
        raise ValueError("source run id must be a positive decimal integer")
    if evidence.get("environment") != "development" or evidence.get("result") != "passed":
        raise ValueError("source evidence is not a successful development deployment")
    if str(evidence.get("run_id")) != source_run_id:
        raise ValueError("source run id does not match development evidence")
    for field, value in requested.items():
        if evidence.get(field) != value:
            raise ValueError(f"{field} differs from successful development deployment")


def check_published_artifact(artifact: dict, requested: dict[str, str]) -> None:
    if artifact != requested:
        raise ValueError("published image artifact differs from requested release")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--api", required=True)
    parser.add_argument("--web", required=True)
    parser.add_argument("--egress", required=True)
    parser.add_argument("--source-run-id")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--published-artifact", type=Path)
    args = parser.parse_args()
    try:
        data = release(args.commit, args.api, args.web, args.egress)
        if args.published_artifact:
            if args.evidence or args.source_run_id:
                raise ValueError("published artifact and promotion evidence are exclusive")
            check_published_artifact(json.loads(args.published_artifact.read_text()), data)
        if args.evidence:
            if not args.source_run_id:
                raise ValueError("source run id is required for promotion")
            check_promotion(json.loads(args.evidence.read_text()), args.source_run_id, data)
        elif args.source_run_id:
            raise ValueError("evidence is required for promotion")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"invalid release: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(data, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
