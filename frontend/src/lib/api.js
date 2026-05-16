/*
  api.js centralizes all static JSON loading.

  In Vite, files inside frontend/public are served from the root path.
  So this file:
    frontend/public/data/latest/analysis_summary.json

  is requested in the browser as:
    /data/latest/analysis_summary.json
*/

const DATA_BASE = "/data/latest";

async function loadJson(fileName) {
  const response = await fetch(`${DATA_BASE}/${fileName}`);

  /*
    fetch() does not automatically throw for 404 or 500 responses.
    Therefore, we check response.ok ourselves.
  */
  if (!response.ok) {
    throw new Error(
      `Failed to load ${fileName}. HTTP ${response.status} ${response.statusText}`
    );
  }

  return response.json();
}

export function loadAnalysisSummary() {
  return loadJson("analysis_summary.json");
}

export function loadTrendSummary() {
  return loadJson("trend_summary.json");
}

export function loadTagSummary() {
  return loadJson("tag_summary.json");
}

export function loadGlobalLeaderboard() {
  return loadJson("global_leaderboard.json");
}

export function loadAffinityRowsWithTrends() {
  return loadJson("affinity_rows_with_trends.json");
}