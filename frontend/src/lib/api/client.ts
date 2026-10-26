// Single entry point for app data. Replace createMockClient with an HTTP client
// against the FastAPI /api/v1 backend without touching components.
import { auth } from "@/lib/auth";
import { createMockClient } from "./mockClient";
import type { ApiClient } from "./types";

export const api: ApiClient = createMockClient(() => auth.getSession()?.user.id ?? null);
