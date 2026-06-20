import { getTrendStatus } from "./filterUtils";
import { formatNumber } from "./formatters";

function firstPresent(...values) {
  return values.find(
    (value) => value !== null && value !== undefined && value !== ""
  );
}

function getNumericValue(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const numericValue = Number(value);

  return Number.isNaN(numericValue) ? null : numericValue;
}

export function normalizeTrendFields(row) {
  const trendStatus = getTrendStatus(row);
  const rankDelta = firstPresent(row.rank_delta, row.rank_within_tag_by_z_delta);

  return {
    ...row,
    trend_status: trendStatus,
    rank_delta: rankDelta ?? null,
  };
}

export function formatSignedDecimal(value, digits = 2) {
  const numericValue = getNumericValue(value);

  if (numericValue === null) {
    return "\u2014";
  }

  const sign = numericValue > 0 ? "+" : "";

  return `${sign}${numericValue.toFixed(digits)}`;
}

export function formatSignedInteger(value) {
  const numericValue = getNumericValue(value);

  if (numericValue === null) {
    return "\u2014";
  }

  if (numericValue > 0) {
    return `+${formatNumber(numericValue)}`;
  }

  if (numericValue < 0) {
    return `-${formatNumber(Math.abs(numericValue))}`;
  }

  return "0";
}
