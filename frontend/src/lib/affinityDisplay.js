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
