const BASE_URL = import.meta.env?.BASE_URL || "/";

function getBasePath() {
  return BASE_URL.endsWith("/") ? BASE_URL : `${BASE_URL}/`;
}

function joinDataPath(relativePath) {
  const cleanRelativePath = String(relativePath).replace(/^\/+/, "");
  return `${getBasePath()}data/latest/${cleanRelativePath}`;
}

export function safeJsonFilename(value) {
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

  const text = await response.text();
  const cleanText = text.trimStart();

  if (cleanText.startsWith("<")) {
    throw new Error(
      `Expected JSON at ${url}, but received HTML. Local data is probably missing. Run npm run data:download from the frontend folder.`
    );
  }

  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`Failed to parse JSON from ${url}: ${error.message}`, {
      cause: error,
    });
  }
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

export async function loadSetIndex() {
  return fetchJson("sets/index.json");
}

export async function loadSetDetail(setCode) {
  const filename = safeJsonFilename(setCode);
  return fetchJson(`sets/${filename}.json`);
}

export async function loadThemeBracketIndex() {
  return fetchJson("theme-brackets/index.json");
}

export async function loadThemeBracketDetail(themeSlug) {
  const filename = safeJsonFilename(themeSlug);
  const theme = await fetchJson(`theme-brackets/${filename}.json`);

  // Validate even empty files: a legacy-gated compact list can omit newly
  // eligible commanders despite a current index. The loader uses complete
  // tag data when an outdated compact file is rejected.
  if (theme?.qualification_score_field !== "z" || !Array.isArray(theme.rows)) {
    throw new Error("The compact theme uses an outdated qualification score or invalid rows.");
  }

  return theme.rows;
}

export async function loadThemeReport() {
  const report = await fetchJson("theme-report.json");

  // Older compact reports omitted rows using legacy scores. The report page
  // falls back to complete tag data when these rows cannot be recovered here.
  if (report?.qualification_score_field !== "z") {
    throw new Error("The compact theme report uses an outdated qualification score.");
  }

  return report;
}

export async function loadQuirkyBuildsReport() {
  return fetchJson("quirky-builds.json");
}

export async function loadLeaderboardIndex() {
  return fetchJson("leaderboard/index.json");
}

export async function loadLeaderboardPage(pageNumber) {
  const safePage = Math.max(1, Number(pageNumber || 1));
  const padded = String(safePage).padStart(4, "0");

  return fetchJson(`leaderboard/page_${padded}.json`);
}
