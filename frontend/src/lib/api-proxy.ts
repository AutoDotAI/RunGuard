/** Forward only the Compose API prefix to its fixed internal service. */
const API_ORIGIN = "http://api:8000";
const REQUEST_HEADERS = ["accept", "authorization", "content-type", "traceparent", "tracestate"];
const STRIP_RESPONSE_HEADERS = ["connection", "content-encoding", "content-length", "keep-alive", "transfer-encoding"];

export async function proxyApiRequest(request: Request): Promise<Response | null> {
  const incoming = new URL(request.url);
  if (incoming.pathname !== "/api/v1" && !incoming.pathname.startsWith("/api/v1/")) return null;

  const target = new URL(API_ORIGIN);
  target.pathname = incoming.pathname;
  target.search = incoming.search;

  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const body = request.method === "GET" || request.method === "HEAD" ? null : request.body;
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    body,
    redirect: "manual",
  };
  if (body) init.duplex = "half";

  try {
    const upstream = await fetch(target, init);
    const responseHeaders = new Headers(upstream.headers);
    for (const name of STRIP_RESPONSE_HEADERS) responseHeaders.delete(name);
    // Keep internal service names out of redirects exposed to the browser.
    const location = responseHeaders.get("location");
    if (location) {
      const redirect = new URL(location, target);
      responseHeaders.set("location", redirect.origin === target.origin
        ? `${redirect.pathname}${redirect.search}${redirect.hash}` : location);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return Response.json({ error: { code: "API_UNAVAILABLE", message: "RunGuard API is unavailable. Please try again." } }, {
      status: 502,
      headers: { "cache-control": "no-store" },
    });
  }
}
