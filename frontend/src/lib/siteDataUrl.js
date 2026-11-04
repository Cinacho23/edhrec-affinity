// A build may point at an immutable Cloudflare version while local development
// and older deployments continue reading same-origin data.
export function siteDataUrl(relativePath, { basePath = "/", dataBaseUrl = "" } = {}) {
  const root = dataBaseUrl || `${basePath.replace(/\/$/, "")}/data/latest`;
  return `${root.replace(/\/+$/, "")}/${String(relativePath).replace(/^\/+/, "")}`;
}

// Only accept a new snapshot of the already configured Worker/account. Polling
// the small Pages manifest keeps open Discovery tabs current after publication.
export function refreshedDataBaseUrl(current, candidate) {
  if (!current || typeof candidate !== "string") return current;
  try {
    const before = new URL(current);
    const after = new URL(candidate);
    const workerHost = (url) => url.hostname.replace(/^[a-f0-9]{8}-/, "");
    if (after.protocol !== "https:" || after.username || after.password || after.port || after.search || after.hash
        || !/^[a-f0-9]{8}-edhrec-affinity-data\.[a-z0-9-]+\.workers\.dev$/.test(after.hostname)
        || workerHost(before) !== workerHost(after) || after.pathname.replace(/\/$/, "") !== "/data/latest") return current;
    return candidate.replace(/\/$/, "");
  } catch {
    return current;
  }
}
