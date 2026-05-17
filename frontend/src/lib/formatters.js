export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return new Intl.NumberFormat("en-US").format(Number(value));
}

export function formatDecimal(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return Number(value).toFixed(digits);
}

export function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const number = Number(value);

  // Backend may provide either decimal 0.037 or display value 3.7.
  const displayValue = Math.abs(number) <= 1 ? number * 100 : number;

  return `${displayValue.toFixed(digits)}%`;
}

export function formatSigned(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const number = Number(value);
  const sign = number > 0 ? "+" : "";

  return `${sign}${number.toFixed(digits)}`;
}

export function formatColorIdentity(value) {
  if (!value) return "Colorless";

  if (Array.isArray(value)) {
    return value.length > 0 ? value.join("") : "Colorless";
  }

  return String(value);
}

export function formatRank(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return `#${Number(value).toLocaleString("en-US")}`;
}