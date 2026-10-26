# Frontend dependency advisory review (2026-10-26)

Issue [#31](https://github.com/AutoDotAI/RunGuard/issues/31) tracks this review. The baseline was the clean RunGuard `main` lockfile at `c098c23`; `npm ci --no-audit --no-fund` installed Vite 8.1.5 and Zod 3.25.76. Both `npm audit --omit=dev --json` and full `npm audit --json` reported **two high** findings. The production audit includes Vite because other declared production dependencies require it; this does not mean the Vite development server is exposed by the RunGuard production container.

| Finding | Baseline and affected range | Reachability and action |
| --- | --- | --- |
| Vite, [GHSA-587h-7wmj-h5pm](https://github.com/advisories/GHSA-587h-7wmj-h5pm) (high), plus [GHSA-698v-4r3x-3vcv](https://github.com/advisories/GHSA-698v-4r3x-3vcv), [GHSA-r825-w8mm-5pxw](https://github.com/advisories/GHSA-r825-w8mm-5pxw), [GHSA-9jrq-w75r-8gcw](https://github.com/advisories/GHSA-9jrq-w75r-8gcw), and [GHSA-rq7h-c2jc-7f22](https://github.com/advisories/GHSA-rq7h-c2jc-7f22) | 8.1.5; npm reported `>=8.0.0 <=8.1.5` for each | The high launch-editor issue concerns a Windows Vite development server with an attacker-controlled editor path. RunGuard's local Linux Compose profile serves the built app, and does not expose the Vite dev server publicly. Local Windows development or a future exposed dev server would be relevant. Upgraded within Vite 8 to 8.3.5; app tests, type check, production build, and image build verify compatibility. |
| Zod, [GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5) (high) | 3.25.76; affected `<4.6.6` | Oversized constrained arrays can exhaust memory during validation. `frontend/src` has no direct Zod import; the dependency is used by TanStack build tooling and is an optional peer of React Hook Form resolvers. No known RunGuard user input reaches a Zod array schema, but a future schema could. Upgraded to 4.6.7 and verified the app. This is a major Zod upgrade, so future direct Zod schemas must use the Zod 4 API. |

After the change, `npm audit --omit=dev --json` and full `npm audit --json` each report **zero** findings at all severities. The npm lockfile is the CI/Docker source of truth and `npm ci --no-audit --no-fund` succeeds. `bun.lock` is retained for the Lovable workflow; Bun 1.4.4 `bun install --frozen-lockfile` succeeds, and both its top-level and nested Zod resolutions are at least 4.6.7. npm and Bun may resolve unrelated permissive dependencies to different versions; each lockfile is independently frozen.

The RunGuard `frontend/` layout and the original Lovable repository history stay intact. When importing a later Lovable export using `_docs/lovable-sync.md`, review and reapply this package/lockfile change if that export reverts dependency versions. RunGuard's npm lockfile remains authoritative for CI and Docker; update both locks when dependency declarations change, and audit both again. Do not force-push or rewrite the Lovable repository.

Reproduction commands from a clean RunGuard checkout:

```bash
cd frontend
npm ci --no-audit --no-fund
npm audit --omit=dev
npm audit
npm test
npx tsc --noEmit
npm run build
npx --yes bun install --frozen-lockfile
```

The full backend and frontend container checks for the change are recorded in the #31 engineering and QA comments.
