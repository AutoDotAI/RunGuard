# Lovable source snapshot

RunGuard is the public project repository. Its `frontend/` directory contains
ordinary tracked React source files so an anonymous clone can build the app.
Lovable editor commits go to the separate, private
`AutoDotAI/screenshot-guard-studio` repository, not to `AutoDotAI/RunGuard`.
RunGuard does not automatically sync changes back to Lovable.

The initial RunGuard snapshot is from Lovable commit
`3fced4a15b1009fcff602a703c8f6efe5f23b107` on `main`. The original
Lovable repository and its published history were not rewritten. In this
workspace the original clean Lovable checkout remains at `frontend/.git`;
that nested Git directory is not tracked by RunGuard and does not appear in a
fresh public clone. Its source files were staged as ordinary blobs in the
RunGuard index, which is why the public repository has a `frontend/` tree
instead of a private gitlink.

## Bring a later Lovable commit into RunGuard

Keep a separate checkout of the Lovable repository. A fresh RunGuard clone
can create one alongside it using AutoDotAI access:

```bash
GH_TOKEN="$(gh auth token -u AutoDotAI)" gh repo clone AutoDotAI/screenshot-guard-studio ../runguard-lovable-source
```

Fetch the latest Lovable commit in that checkout, then compare it with the
source hash recorded above. From a **clean RunGuard checkout** at the project
root, apply only the Lovable change to `frontend/`:

```bash
git -C ../runguard-lovable-source fetch origin main
git -C ../runguard-lovable-source diff --binary 3fced4a15b1009fcff602a703c8f6efe5f23b107 origin/main -- . | git apply --index --directory=frontend -
```

If working in this original workspace, where `frontend/` itself remains a
Lovable checkout and its files have already advanced to the newer commit,
use `git apply --cached --directory=frontend -` in the second command so the
patch updates only RunGuard's index. Do not run `git add frontend/` while the
nested `.git` is present; it may stage a gitlink instead of the source. Review
`git diff --cached`, run frontend build and tests, update the recorded source
hash in this document, and commit the snapshot to RunGuard. Never force push,
rebase, amend, or squash published Lovable commits.

Run the frontend from `frontend/` with `npm ci --no-audit --no-fund`,
`npm run build`, `npm test`, and `npm run dev`. Run the API from `backend/`
with `uv sync --locked --extra test`, `uv run pytest -q`, and
`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`. The API stores
accounts, workers, runs, and logs in SQLite; the frontend uses its HTTP API.
