/*
  Small formatting helpers keep page components cleaner.
*/

export function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }

  return Number(value).toLocaleString();
}

export function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }

  /*
    The analysis pipeline stores tag_affinity_pct as a decimal.
    Example:
      0.037 = 3.7%

    Some rows may also contain display versions elsewhere, but this function
    assumes the formula field is decimal.
  */
  return `${(Number(value) * 100).toFixed(2)}%`;
}

export function formatZScore(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }

  return Number(value).toFixed(2);
}

export function formatSignedNumber(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }

  const numeric = Number(value);

  if (numeric > 0) {
    return `+${numeric.toLocaleString()}`;
  }

  return numeric.toLocaleString();
}

export function formatDateLike(value) {
  if (!value) {
    return "Unavailable";
  }

  return String(value);
}