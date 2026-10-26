"""Render short-lived Compose overrides for the two deployment modes.

The committed production base retains build contexts for the isolated security
probe. A release must remove them and use pinned digests. The rollback rehearsal
uses prebuilt local A/B images without rebuilding during its state transitions.
"""

import argparse


RELEASE = """services:
  migrate:
    image: ${RUNGUARD_API_IMAGE:?Set the tested API image digest}
    build: !reset null
  api:
    image: ${RUNGUARD_API_IMAGE:?Set the tested API image digest}
    build: !reset null
  worker:
    image: ${RUNGUARD_API_IMAGE:?Set the tested API image digest}
    build: !reset null
  web:
    image: ${RUNGUARD_WEB_IMAGE:?Set the tested web image digest}
    build: !reset null
  egress_guard:
    image: ${RUNGUARD_EGRESS_IMAGE:?Set the tested egress image digest}
    build: !reset null
"""

REHEARSAL = """services:
  migrate:
    build: !reset null
  api:
    build: !reset null
  worker:
    build: !reset null
  web:
    build: !reset null
  egress_guard:
    image: runguard-egress:${RUNGUARD_IMAGE_TAG:?Set local rehearsal release A or B}
    build: !reset null
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("release", "rehearsal"))
    args = parser.parse_args()
    print(RELEASE if args.mode == "release" else REHEARSAL, end="")


if __name__ == "__main__":
    main()
