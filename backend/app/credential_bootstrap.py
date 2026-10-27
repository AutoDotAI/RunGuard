"""Create or verify the private model encryption key after database migration."""

from pathlib import Path

from .ai_credentials import ensure_model_key
from .db import UserRepository


def main() -> int:
    users = UserRepository()
    try:
        ensure_model_key(users, Path("/run/runguard-secrets/model"))
    finally:
        users.close()
    print("Model credential encryption ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
