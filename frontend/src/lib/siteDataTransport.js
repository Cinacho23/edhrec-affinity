async function decodeBrotli(bytes) {
  const decoder = await import("./brotliDecoder.js");
  return decoder.decodeBrotli(bytes);
}

// Production uses stored Brotli resources to fit Pages' published-site limit.
// Plain JSON remains supported for local data and previous deployments.
export async function fetchSiteJson(url, {
  optional = false, signal, cache, preferBrotli = false, decode = decodeBrotli,
} = {}) {
  const candidates = preferBrotli ? [`${url}.br`, url] : [url, `${url}.br`];
  const options = signal || cache
    ? { ...(signal ? { signal } : {}), ...(cache ? { cache } : {}) } : undefined;
  for (const candidate of candidates) {
    const response = await fetch(candidate, options);
    if (response.status === 404) continue;
    if (!response.ok) throw new Error(`Failed to load ${candidate}. HTTP ${response.status}`);

    let text;
    if (candidate.endsWith(".br") && response.headers?.get("content-encoding") !== "br") {
      if (response.headers?.get("content-type")?.includes("text/html")) continue;
      const bytes = new Uint8Array(await response.arrayBuffer());
      // Hosts with an SPA fallback can return an HTML shell with HTTP 200.
      if (/^\s*<(?:!doctype|html\b)/i.test(new TextDecoder().decode(bytes.subarray(0, 100)))) continue;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(await decode(bytes));
      } catch (error) {
        throw new Error(`Failed to decode Brotli JSON from ${candidate}: ${error.message}`, { cause: error });
      }
    } else {
      text = await response.text();
    }
    signal?.throwIfAborted();
    if (text.trimStart().startsWith("<")) continue;
    try {
      return JSON.parse(text);
    } catch (error) {
      throw new Error(`Failed to parse JSON from ${candidate}: ${error.message}`, { cause: error });
    }
  }
  if (optional) return null;
  throw new Error(`Expected JSON at ${url}, but data is missing or the host returned HTML. `
    + "Run npm run data:download from the frontend folder for local development.");
}
