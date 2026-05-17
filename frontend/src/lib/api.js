const BASE_URL = import.meta.env.BASE_URL || "/";

function getBasePath() {
  return BASE_URL.endsWith("/") ? BASE_URL : `${BASE_URL}/`;
}

function joinDataPath(relativePath) {
  const cleanRelativePath = String(relativePath).replace(/^\/+/, "");
  return `${getBasePath()}data/latest/${cleanRelativePath}`;
}

function safeJsonFilename(value) {
  return String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "") || "unknown";
}

async function fetchJson(relativePath) {
  const url = joinDataPath(relativePath);
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Failed to load ${url}. HTTP ${response.status}`);
  }
  
  return response.json();
}

export function getDataUrl(relativePath) {
  return joinDataPath(relativePath);
}

export async function loadSiteManifest() {
  return fetchJson("site_manifest.json");
}

export async function loadAnalysisSummary() {
  return fetchJson("summaries/analysis_summary.json");
}

export async function loadTrendSummary() {
  return fetchJson("summaries/trend_summary.json");
}

export async function loadTagSummary() {
  return fetchJson("summaries/tag_summary.json");
}

export async function loadCommanderIndex() {
  return fetchJson("commanders/index.json");
}

export async function loadCommanderDetail(commanderSlug) {
  const filename = safeJsonFilename(commanderSlug);
  return fetchJson(`commanders/${filename}.json`);
}

export async function loadTagIndex() {
  return fetchJson("tags/index.json");
}

export async function loadTagDetail(tagSlug) {
  const filename = safeJsonFilename(tagSlug);
  return fetchJson(`tags/${filename}.json`);
}

export async function loadLeaderboardIndex() {
  return fetchJson("leaderboard/index.json");
}

export async function loadLeaderboardPage(pageNumber) {
  const safePage = Math.max(1, Number(pageNumber || 1));
  const padded = String(safePage).padStart(4, "0");

  return fetchJson(`leaderboard/page_${padded}.json`);
}