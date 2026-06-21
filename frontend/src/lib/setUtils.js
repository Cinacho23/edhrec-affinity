import { safeJsonFilename } from "./api";

function normalizeList(value) {
  if (!value) return [];

  if (Array.isArray(value)) {
    return value;
  }

  if (typeof value === "string") {
    const text = value.trim();

    if (!text) {
      return [];
    }

    if (text.startsWith("[") && text.endsWith("]")) {
      try {
        const parsed = JSON.parse(text);
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    }

    return text
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  }

  return [];
}

function normalizeOriginSet(originSet) {
  if (!originSet || typeof originSet !== "object") {
    return null;
  }

  const setCode = String(
    originSet.set_code || originSet.code || originSet.origin_set_code || ""
  )
    .trim()
    .toLowerCase();

  if (!setCode) {
    return null;
  }

  return {
    set_code: setCode,
    set_name:
      originSet.set_name ||
      originSet.name ||
      originSet.origin_set_name ||
      setCode.toUpperCase(),
    released_at:
      originSet.released_at || originSet.origin_released_at || null,
    scryfall_set_uri:
      originSet.scryfall_set_uri || originSet.scryfall_uri || null,
    set_uri: originSet.set_uri || null,
  };
}

export function extractSetCodeFromScryfallUri(uri) {
  const match = String(uri || "").match(/\/card\/([^/]+)\//i);
  return match ? safeJsonFilename(match[1]) : "";
}

export function getCommanderOriginSets(commander) {
  const originSets = [];
  const seenCodes = new Set();

  function addOrigin(originSet) {
    const normalized = normalizeOriginSet(originSet);

    if (!normalized || seenCodes.has(normalized.set_code)) {
      return;
    }

    seenCodes.add(normalized.set_code);
    originSets.push(normalized);
  }

  for (const originSet of normalizeList(commander?.origin_sets)) {
    addOrigin(originSet);
  }

  addOrigin({
    set_code: commander?.origin_set_code,
    set_name: commander?.origin_set_name,
    released_at: commander?.origin_released_at,
    scryfall_set_uri: commander?.scryfall_set_uri,
    set_uri: commander?.set_uri,
  });

  const uris = [
    commander?.scryfall_uri,
    ...normalizeList(commander?.partner_scryfall_uris),
  ];

  for (const uri of uris) {
    const setCode = extractSetCodeFromScryfallUri(uri);

    if (setCode) {
      addOrigin({
        set_code: setCode,
        set_name: setCode.toUpperCase(),
      });
    }
  }

  return originSets;
}

export function formatOriginSets(commander) {
  const originSets = getCommanderOriginSets(commander);

  if (originSets.length === 0) {
    return "Unknown set";
  }

  return originSets
    .map((originSet) => originSet.set_name || originSet.set_code.toUpperCase())
    .join(", ");
}

export function rowBelongsToSet(row, setCode) {
  const normalizedSetCode = safeJsonFilename(setCode);

  return getCommanderOriginSets(row).some(
    (originSet) => safeJsonFilename(originSet.set_code) === normalizedSetCode
  );
}

export function rowHasTagMetrics(row) {
  return [
    row?.tag_name,
    row?.tag_slug,
    row?.z,
    row?.tag_decks,
    row?.tag_affinity_pct,
  ].some((value) => value !== null && value !== undefined && value !== "");
}

export function buildSetIndexFromCommanders(commanders) {
  const setMap = new Map();

  for (const commander of commanders || []) {
    for (const originSet of getCommanderOriginSets(commander)) {
      const current = setMap.get(originSet.set_code);

      if (!current) {
        setMap.set(originSet.set_code, {
          set_code: originSet.set_code,
          set_name: originSet.set_name || originSet.set_code.toUpperCase(),
          released_at: originSet.released_at || null,
          scryfall_set_uri: originSet.scryfall_set_uri || null,
          set_uri: originSet.set_uri || null,
          commander_count: 1,
          file: `sets/${safeJsonFilename(originSet.set_code)}.json`,
        });
        continue;
      }

      current.commander_count += 1;

      if (!current.released_at && originSet.released_at) {
        current.released_at = originSet.released_at;
      }

      if (!current.scryfall_set_uri && originSet.scryfall_set_uri) {
        current.scryfall_set_uri = originSet.scryfall_set_uri;
      }
    }
  }

  return [...setMap.values()].sort((a, b) => {
    const releasedCompare = String(b.released_at || "").localeCompare(
      String(a.released_at || "")
    );

    if (releasedCompare !== 0) {
      return releasedCompare;
    }

    return String(a.set_name || "").localeCompare(String(b.set_name || ""));
  });
}

export function getCommandersForSet(commanders, setCode) {
  const normalizedSetCode = safeJsonFilename(setCode);

  return (commanders || [])
    .filter((commander) =>
      getCommanderOriginSets(commander).some(
        (originSet) => safeJsonFilename(originSet.set_code) === normalizedSetCode
      )
    )
    .map((commander) => {
      const selectedOriginSet = getCommanderOriginSets(commander).find(
        (originSet) => safeJsonFilename(originSet.set_code) === normalizedSetCode
      );

      return {
        ...commander,
        origin_set_code: selectedOriginSet?.set_code || normalizedSetCode,
        origin_set_name:
          selectedOriginSet?.set_name || normalizedSetCode.toUpperCase(),
        origin_released_at: selectedOriginSet?.released_at || null,
      };
    });
}
