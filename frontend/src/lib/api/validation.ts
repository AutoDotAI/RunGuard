export function validateName(name: string): string | null {
  const n = name.trim();
  if (n.length < 1) return "Name is required.";
  if (n.length > 80) return "Name must be 80 characters or fewer.";
  return null;
}

const PRIVATE_HOST = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|0\.0\.0\.0|\[?::1\]?$)/i;

export function validateUrl(url: string): string | null {
  const u = url.trim();
  if (!u) return "URL is required.";
  if (u.length > 2048) return "URL must be 2,048 characters or fewer.";
  let parsed: URL;
  try {
    parsed = new URL(u);
  } catch {
    return "Enter a valid URL, e.g. https://example.com";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "Only http(s) URLs are allowed.";
  if (parsed.username || parsed.password) return "URLs with credentials are not allowed.";
  if (PRIVATE_HOST.test(parsed.hostname)) return "Private or local addresses are blocked.";
  return null;
}
