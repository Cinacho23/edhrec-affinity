export function getValue(row, key) {
  const value = row?.[key];

  if (value === null || value === undefined) {
    return null;
  }

  return value;
}

export function asNumber(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  const number = Number(value);

  return Number.isNaN(number) ? null : number;
}

export function compareValues(a, b, direction = "desc") {
  const aNumber = asNumber(a);
  const bNumber = asNumber(b);

  let result;

  if (aNumber !== null || bNumber !== null) {
    if (aNumber === null) result = 1;
    else if (bNumber === null) result = -1;
    else result = aNumber - bNumber;
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

  const rowValue = asNumber(row?.[key]);
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

  const rowValue = asNumber(row?.[key]);
  const filterValue = asNumber(maxValue);

  if (filterValue === null) {
    return true;
  }

  if (rowValue === null) {
    return false;
  }

  return rowValue <= filterValue;
}
