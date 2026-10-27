import { afterEach, describe, expect, it, vi } from "vitest";

import { proxyApiRequest } from "./api-proxy";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fixed Compose API proxy", () => {
  it("forwards a worker write through the fixed internal API host", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"id":"worker-1"}', {
      status: 201,
      headers: { "content-type": "application/json", "x-request-id": "req-1" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const body = JSON.stringify({ name: "Project monitoring", url: "https://github.com/AutoDotAI/RunGuard" });
    const request = new Request("http://127.0.0.1:3000/api/v1/workers?view=full", {
      method: "POST",
      headers: {
        authorization: "Bearer fake-test-token",
        "content-type": "application/json",
        "x-forwarded-host": "attacker.example",
      },
      body,
    });

    const response = await proxyApiRequest(request);
    expect(response?.status).toBe(201);
    expect(await response?.json()).toEqual({ id: "worker-1" });
    expect(response?.headers.get("x-request-id")).toBe("req-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [target, init] = fetchMock.mock.calls[0]!;
    expect(target.toString()).toBe("http://api:8000/api/v1/workers?view=full");
    expect(init.method).toBe("POST");
    expect(init.headers.get("authorization")).toBe("Bearer fake-test-token");
    expect(init.headers.get("content-type")).toBe("application/json");
    expect(init.headers.get("x-forwarded-host")).toBeNull();
    expect(await new Response(init.body).text()).toBe(body);
  });

  it("ignores unrelated paths and does not become a general-purpose proxy", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(await proxyApiRequest(new Request("http://127.0.0.1:3000/workers/new"))).toBeNull();
    expect(await proxyApiRequest(new Request("http://127.0.0.1:3000/api/v10/workers"))).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns a sanitized JSON 502 when the internal API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connect to http://api:8000 failed; token=secret")));
    const response = await proxyApiRequest(new Request("http://127.0.0.1:3000/api/v1/workers", {
      method: "POST", body: "private request body",
    }));
    expect(response?.status).toBe(502);
    expect(response?.headers.get("content-type")).toContain("application/json");
    const body = await response?.text();
    expect(body).toContain("API_UNAVAILABLE");
    expect(body).not.toContain("api:8000");
    expect(body).not.toContain("private request body");
    expect(body).not.toContain("secret");
  });

  it("keeps a same-service redirect on the browser origin", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, {
      status: 307, headers: { location: "http://api:8000/api/v1/workers/?page=2" },
    })));
    const response = await proxyApiRequest(new Request("http://127.0.0.1:3000/api/v1/workers"));
    expect(response?.status).toBe(307);
    expect(response?.headers.get("location")).toBe("/api/v1/workers/?page=2");
  });
});
