"""Parse both Actions workflows and reject duplicate keys or broken job structure.

PyYAML's BaseLoader keeps the YAML 1.1 word ``on`` as a string. This is a
host-free syntax/shape check; GitHub still validates Actions semantics on push.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


class UniqueKeyLoader(yaml.BaseLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def validate(path: Path) -> None:
    source = path.read_text()
    if source.count("${{") != source.count("}}"):
        raise ValueError("unbalanced Actions expression delimiters")
    workflow = yaml.load(source, Loader=UniqueKeyLoader)
    if not isinstance(workflow, dict) or not workflow.get("name"):
        raise ValueError("workflow needs a name")
    if not isinstance(workflow.get("on"), dict) or not workflow["on"]:
        raise ValueError("workflow needs event triggers")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("workflow needs jobs")
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or not job.get("runs-on"):
            raise ValueError(f"{job_id}: missing runner")
        steps = job.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"{job_id}: missing steps")
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or ("uses" in step) == ("run" in step):
                raise ValueError(f"{job_id}: step {index} needs exactly one of uses/run")
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        if not isinstance(needs, list) or any(dependency not in jobs for dependency in needs):
            raise ValueError(f"{job_id}: unknown job dependency")
    print(f"PASS: parsed workflow {path}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: validate_workflows.py workflow.yml ...", file=sys.stderr)
        return 2
    try:
        for name in sys.argv[1:]:
            validate(Path(name))
    except (OSError, yaml.YAMLError, ValueError, TypeError) as exc:
        print(f"invalid workflow: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
