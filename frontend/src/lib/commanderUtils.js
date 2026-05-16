/*
  commanderUtils.js

  This file converts the row-based affinity table into commander-focused data.

  Important:
  - The backend analysis output is one row per commander-tag pair.
  - The commander search page needs one object per commander.
  - The commander detail page needs all tag rows for one commander.
  - After Scryfall enrichment, rows may include:
      color_identity
      card_image_url
      partner_card_image_urls
      partner_scryfall_uris
      scryfall_card_names

  This file normalizes old/new backend field names so frontend components do
  not need to care whether the row came from Chat 7 analysis output or Chat 8
  trend-merged output.
*/

export function firstNonEmpty(...values) {
  for (const value of values) {
    if (value !== null && value !== undefined && value !== "") {
      return value;
    }
  }

  return null;
}

export function getNumeric(value, fallback = null) {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }

  const numeric = Number(value);

  if (Number.isNaN(numeric)) {
    return fallback;
  }

  return numeric;
}

function uniqueNonEmpty(values) {
  const seen = new Set();
  const result = [];

  for (const value of values) {
    if (!value) {
      continue;
    }

    const normalized = String(value).trim();

    if (!normalized || seen.has(normalized)) {
      continue;
    }

    seen.add(normalized);
    result.push(normalized);
  }

  return result;
}

function normalizeArray(value) {
  /*
    Keeps arrays as arrays.

    Converts comma-separated strings into arrays.

    Returns [] for missing values.
  */
  if (Array.isArray(value)) {
    return value.filter((item) => item !== null && item !== undefined && item !== "");
  }

  if (typeof value === "string") {
    const trimmed = value.trim();

    if (!trimmed) {
      return [];
    }

    if (trimmed.includes(",")) {
      return trimmed
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
    }

    return [trimmed];
  }

  return [];
}

function normalizeColorIdentity(value) {
  if (Array.isArray(value)) {
    return value;
  }

  if (typeof value === "string") {
    const trimmed = value.trim();

    if (!trimmed) {
      return null;
    }

    if (trimmed.includes(",")) {
      return trimmed
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean);
    }

    if (/^[WUBRG]+$/.test(trimmed)) {
      return trimmed.split("");
    }

    return trimmed;
  }

  return null;
}

function normalizeAffinityDecimal(row) {
  const decimalValue = firstNonEmpty(
    row.affinity_pct_current,
    row.tag_affinity_pct,
    row.tag_affinity_pct_x,
    row.tag_affinity_pct_y,
    row.pct
  );

  const numericDecimal = getNumeric(decimalValue, null);

  if (numericDecimal !== null) {
    return numericDecimal;
  }

  const displayValue = getNumeric(row.tag_affinity_pct_display, null);

  if (displayValue !== null) {
    return displayValue / 100;
  }

  return null;
}

function getPartnerImageUrls(row) {
  /*
    partner_card_image_urls is the preferred field from Scryfall enrichment.

    We also accept alternate names in case you later rename fields in the
    backend.
  */
  const partnerImages = [
    ...normalizeArray(row.partner_card_image_urls),
    ...normalizeArray(row.partner_image_urls),
    ...normalizeArray(row.card_image_urls),
  ];

  return uniqueNonEmpty(partnerImages);
}

function getPartnerScryfallUris(row) {
  return uniqueNonEmpty([
    ...normalizeArray(row.partner_scryfall_uris),
    ...normalizeArray(row.scryfall_uris),
  ]);
}

function getScryfallCardNames(row) {
  return uniqueNonEmpty([
    ...normalizeArray(row.scryfall_card_names),
    ...normalizeArray(row.partner_card_names),
  ]);
}

export function normalizeAffinityRow(row) {
  const partnerCardImageUrls = getPartnerImageUrls(row);
  const cardImageUrl = firstNonEmpty(
    row.card_image_url,
    row.scryfall_image_url,
    row.image_url,
    row.commander_image_url,
    partnerCardImageUrls[0]
  );

  return {
    ...row,

    commander_name: firstNonEmpty(row.commander_name, row.commander),
    commander_slug: row.commander_slug,

    tag_name: firstNonEmpty(row.tag_name, row.tag),
    tag_slug: row.tag_slug,

    total_decks: firstNonEmpty(
      row.total_decks_current,
      row.total_decks,
      row.total_decks_x,
      row.total_decks_y
    ),

    tag_decks: firstNonEmpty(
      row.tag_decks_current,
      row.tag_decks,
      row.tag_decks_x,
      row.tag_decks_y
    ),

    tag_affinity_pct: normalizeAffinityDecimal(row),

    z: firstNonEmpty(row.z_current, row.z, row.z_x, row.z_y),

    rank_within_tag_by_z: firstNonEmpty(
      row.rank_within_tag_by_z_current,
      row.rank_within_tag_by_z,
      row.rank_within_tag_by_z_x,
      row.rank_within_tag_by_z_y,
      row.rank_within_tag
    ),

    color_identity: normalizeColorIdentity(
      firstNonEmpty(
        row.color_identity,
        row.scryfall_color_identity,
        row.Colour,
        row.colour,
        row.color
      )
    ),

    card_image_url: cardImageUrl,

    partner_card_image_urls:
      partnerCardImageUrls.length > 0
        ? partnerCardImageUrls
        : uniqueNonEmpty([cardImageUrl]),

    scryfall_uri: firstNonEmpty(row.scryfall_uri, row.commander_scryfall_uri),

    partner_scryfall_uris: getPartnerScryfallUris(row),

    scryfall_card_names: getScryfallCardNames(row),

    trend_status: firstNonEmpty(
      row.snapshot_status,
      row.trend_status,
      row.status_type
    ),

    rank_delta: firstNonEmpty(row.rank_delta, row.rank_within_tag_by_z_delta),
  };
}

function getBestTagRows(rows, limit = 5) {
  return [...rows]
    .sort((a, b) => {
      const zA = getNumeric(a.z, Number.NEGATIVE_INFINITY);
      const zB = getNumeric(b.z, Number.NEGATIVE_INFINITY);

      if (zB !== zA) {
        return zB - zA;
      }

      return getNumeric(b.tag_decks, 0) - getNumeric(a.tag_decks, 0);
    })
    .slice(0, limit);
}

export function groupRowsByCommander(rows) {
  const commanderMap = new Map();

  for (const rawRow of rows) {
    const row = normalizeAffinityRow(rawRow);
    const commanderSlug = row.commander_slug;

    if (!commanderSlug) {
      continue;
    }

    if (!commanderMap.has(commanderSlug)) {
      commanderMap.set(commanderSlug, {
        commander_slug: commanderSlug,
        commander_name: row.commander_name || commanderSlug,
        total_decks: row.total_decks,
        color_identity: row.color_identity,
        card_image_url: row.card_image_url,
        partner_card_image_urls: row.partner_card_image_urls,
        scryfall_uri: row.scryfall_uri,
        partner_scryfall_uris: row.partner_scryfall_uris,
        scryfall_card_names: row.scryfall_card_names,
        trend_status: row.trend_status,
        best_rank_delta: null,
        rows: [],
      });
    }

    const commander = commanderMap.get(commanderSlug);

    commander.rows.push(row);

    if (getNumeric(row.total_decks, 0) > getNumeric(commander.total_decks, 0)) {
      commander.total_decks = row.total_decks;
    }

    if (!commander.card_image_url && row.card_image_url) {
      commander.card_image_url = row.card_image_url;
    }

    if (
      (!commander.partner_card_image_urls ||
        commander.partner_card_image_urls.length <= 1) &&
      row.partner_card_image_urls &&
      row.partner_card_image_urls.length > 1
    ) {
      commander.partner_card_image_urls = row.partner_card_image_urls;
    }

    if (!commander.color_identity && row.color_identity) {
      commander.color_identity = row.color_identity;
    }

    if (!commander.scryfall_uri && row.scryfall_uri) {
      commander.scryfall_uri = row.scryfall_uri;
    }

    if (
      (!commander.partner_scryfall_uris ||
        commander.partner_scryfall_uris.length <= 1) &&
      row.partner_scryfall_uris &&
      row.partner_scryfall_uris.length > 1
    ) {
      commander.partner_scryfall_uris = row.partner_scryfall_uris;
    }

    if (
      (!commander.scryfall_card_names ||
        commander.scryfall_card_names.length <= 1) &&
      row.scryfall_card_names &&
      row.scryfall_card_names.length > 1
    ) {
      commander.scryfall_card_names = row.scryfall_card_names;
    }

    const rankDelta = getNumeric(row.rank_delta, null);

    if (
      rankDelta !== null &&
      (commander.best_rank_delta === null ||
        rankDelta > commander.best_rank_delta)
    ) {
      commander.best_rank_delta = rankDelta;
    }

    if (!commander.trend_status && row.trend_status) {
      commander.trend_status = row.trend_status;
    }
  }

  return [...commanderMap.values()]
    .map((commander) => ({
      ...commander,
      top_tags: getBestTagRows(commander.rows, 5),
    }))
    .sort((a, b) => {
      return getNumeric(b.total_decks, 0) - getNumeric(a.total_decks, 0);
    });
}

export function filterCommanders(commanders, query) {
  const normalizedQuery = query.trim().toLowerCase();

  if (!normalizedQuery) {
    return commanders;
  }

  return commanders.filter((commander) => {
    return (
      String(commander.commander_name ?? "")
        .toLowerCase()
        .includes(normalizedQuery) ||
      String(commander.commander_slug ?? "")
        .toLowerCase()
        .includes(normalizedQuery)
    );
  });
}

export function getCommanderBySlug(rows, commanderSlug) {
  const normalizedRows = rows
    .map(normalizeAffinityRow)
    .filter((row) => row.commander_slug === commanderSlug);

  if (normalizedRows.length === 0) {
    return null;
  }

  const grouped = groupRowsByCommander(normalizedRows);

  return grouped[0] ?? null;
}