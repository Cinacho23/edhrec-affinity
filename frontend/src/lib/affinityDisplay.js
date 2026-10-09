function probability(value) {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }

  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : null;
}

function finiteMetric(value) {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }

  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizedAffinityMetrics(fields) {
  const rank = finiteMetric(fields.rank_within_tag_by_z);
  return {
    ...fields,
    tag_affinity_pct: probability(fields.tag_affinity_pct),
    tag_affinity_adjusted_pct: probability(fields.tag_affinity_adjusted_pct),
    tag_affinity_lower_pct: probability(fields.tag_affinity_lower_pct),
    tag_affinity_upper_pct: probability(fields.tag_affinity_upper_pct),
    z: finiteMetric(fields.z),
    rank_within_tag_by_z: rank !== null && Number.isInteger(rank) && rank > 0 ? rank : null,
  };
}

export function getDecisionAffinityMetrics(row) {
  return normalizedAffinityMetrics({
    tag_affinity_pct: row?.decision_tag_affinity_pct,
    tag_affinity_adjusted_pct: row?.decision_tag_affinity_adjusted_pct,
    tag_affinity_lower_pct: row?.decision_tag_affinity_lower_pct,
    tag_affinity_upper_pct: row?.decision_tag_affinity_upper_pct,
    affinity_model_status: row?.decision_affinity_model_status,
    affinity_model_version: row?.decision_affinity_model_version,
    z: row?.decision_affinity_z,
    rank_within_tag_by_z: row?.decision_rank_within_tag_by_z,
  });
}

export function getThemeAffinityMetrics(row) {
  return normalizedAffinityMetrics({
    tag_affinity_pct: row?.theme_affinity_pct,
    tag_affinity_adjusted_pct: row?.theme_affinity_adjusted_pct,
    tag_affinity_lower_pct: row?.theme_affinity_lower_pct,
    tag_affinity_upper_pct: row?.theme_affinity_upper_pct,
    affinity_model_status: row?.theme_affinity_model_status,
    affinity_model_version: row?.theme_affinity_model_version,
    z: row?.theme_affinity_z,
    rank_within_tag_by_z: row?.theme_rank_within_tag_by_z,
  });
}

export function formatAffinityProbability(value, digits = 2) {
  const number = probability(value);
  return number === null ? "—" : `${(number * 100).toFixed(digits)}%`;
}

export function formatAdjustedAffinity(row) {
  const display = formatAffinityProbability(row?.tag_affinity_adjusted_pct);
  if (display === "—") return display;

  return row?.affinity_model_status && row.affinity_model_status !== "fitted"
    ? `${display} (unadjusted)`
    : display;
}

export function formatAffinityInterval(row) {
  const lower = probability(row?.tag_affinity_lower_pct);
  const upper = probability(row?.tag_affinity_upper_pct);

  if (lower === null || upper === null || lower > upper) return "—";

  return `${formatAffinityProbability(lower)}–${formatAffinityProbability(upper)}`;
}
