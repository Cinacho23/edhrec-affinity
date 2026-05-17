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

  let result = 0;

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

export function rowMatchesText(row, query, fields) {
  const cleanQuery = query.trim().toLowerCase();

  if (!cleanQuery) {
    return true;
  }

  return fields.some((field) =>
    String(row?.[field] ?? "")
      .toLowerCase()
      .includes(cleanQuery)
  );
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