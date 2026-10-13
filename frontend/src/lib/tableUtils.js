import {
  getBuildRarity,
  getDecisionAffinityMetrics,
  getThemeAffinityMetrics,
} from "./affinityDisplay.js";

export function getValue(row, key) {
  if (key === "build_rarity") return getBuildRarity(row);
  if (key === "decision_build_rarity") return getBuildRarity(getDecisionAffinityMetrics(row));
  if (key === "theme_build_rarity") return getBuildRarity(getThemeAffinityMetrics(row));

  const value = row?.[key];

  if (value === null || value === undefined) {
    return null;
  }

  return value;
}

export function asNumber(value) {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }

  const number = Number(value);

  return Number.isFinite(number) ? number : null;
}

export function compareValues(a, b, direction = "desc") {
  const isMissing = (value) =>
    value === null ||
    value === undefined ||
    (typeof value === "string" && value.trim() === "") ||
    (typeof value === "number" && !Number.isFinite(value));
  const aMissing = isMissing(a);
  const bMissing = isMissing(b);

  // Unavailable estimates should never outrank scored rows, in either direction.
  if (aMissing || bMissing) {
    if (aMissing && bMissing) return 0;
    return aMissing ? 1 : -1;
  }

  const aNumber = asNumber(a);
  const bNumber = asNumber(b);

  let result;

  if (aNumber !== null || bNumber !== null) {
    if (aNumber === null) return 1;
    if (bNumber === null) return -1;
    result = aNumber - bNumber;
  } else {
    result = String(a ?? "").localeCompare(String(b ?? ""));
  }

  return direction === "asc" ? result : -result;
}

export function sortRows(rows, sortKey, sortDirection = "desc") {
  if (!sortKey) {
    return rows;
  }

  return [...rows].sort((a, b) =>
    compareValues(getValue(a, sortKey), getValue(b, sortKey), sortDirection)
  );
}

export function toggleSortDirection(currentKey, nextKey, currentDirection) {
  if (currentKey !== nextKey) {
    return "desc";
  }

  return currentDirection === "desc" ? "asc" : "desc";
}

function normalizeSearchText(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

export function rowMatchesText(row, query, fields) {
  const cleanQuery = normalizeSearchText(query);

  if (!cleanQuery) {
    return true;
  }

  const compactQuery = cleanQuery.replace(/\s+/g, "");

  return fields.some((field) => {
    const normalizedValue = normalizeSearchText(row?.[field]);
    const compactValue = normalizedValue.replace(/\s+/g, "");

    return (
      normalizedValue.includes(cleanQuery) ||
      compactValue.includes(compactQuery)
    );
  });
}

export function passesMin(row, key, minValue) {
  if (minValue === "" || minValue === null || minValue === undefined) {
    return true;
  }

  const rowValue = asNumber(getValue(row, key));
  const filterValue = asNumber(minValue);

  if (filterValue === null) {
    return true;
  }

  if (rowValue === null) {
    return false;
  }

  return rowValue >= filterValue;
}

export function passesMax(row, key, maxValue) {
  if (maxValue === "" || maxValue === null || maxValue === undefined) {
    return true;
  }

  const rowValue = asNumber(getValue(row, key));
  const filterValue = asNumber(maxValue);

  if (filterValue === null) {
    return true;
  }

  if (rowValue === null) {
    return false;
  }

  return rowValue <= filterValue;
}
