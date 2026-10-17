import { loadCommanderDetail, loadCommanderIndex } from "./api.js";

const lookupCache = new Map();
const detailCache = new Map();
const previewCache = new Map();
let scryfallQueue = Promise.resolve();
let nextScryfallRequest = 0;
let commanderIndexPromise;

function cleanName(value) {
  return typeof value === "string"
    ? value.replace(/\s*\(Commander\)\s*$/i, "").trim()
    : "";
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.trim()) return [];
  // Card names contain commas; only JSON arrays are safe to split.
  if (value.trim().startsWith("[")) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed;
    } catch { /* Keep malformed legacy values as a single value. */ }
  }
  return [value];
}

function preferredArray(...values) {
  return values.map(asArray).find((value) => value.length) || [];
}

function imageUrl(value) {
  return value?.normal || value?.large || value?.png || value?.small || null;
}

function scryfallId(value) {
  if (typeof value !== "string") return null;
  return value.match(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i)?.[0] || null;
}

function withoutQuery(value) {
  return typeof value === "string" ? value.split("?")[0] : "";
}

function normalizeCard(card, fallbackName = "") {
  if (!card || typeof card !== "object") return null;
  const name = cleanName(card.name) || fallbackName;
  const rawFaces = asArray(card.faces || card.card_faces);
  const faces = rawFaces.map((face) => ({
    name: cleanName(face?.name) || name,
    image_url: face?.image_url || imageUrl(face?.image_uris),
  })).filter((face) => face.image_url);
  // Split/adventure cards have one physical image at the card level.
  if (!faces.length) {
    const image = card.image_url || card.card_image_url || imageUrl(card.image_uris);
    if (image) faces.push({ name, image_url: image });
  }
  const seenImages = new Set();
  return {
    name,
    scryfall_uri: card.scryfall_uri || null,
    faces: faces.filter((face) => {
      const key = withoutQuery(face.image_url);
      if (seenImages.has(key)) return false;
      seenImages.add(key);
      return true;
    }),
  };
}

function cardKeys(card) {
  return [
    card.name && `name:${card.name.toLowerCase()}`,
    card.scryfall_uri && `uri:${withoutQuery(card.scryfall_uri)}`,
    ...card.faces.map((face) => {
      const id = scryfallId(face.image_url);
      return id ? `id:${id}` : `image:${withoutQuery(face.image_url)}`;
    }),
  ].filter(Boolean);
}

function uniqueCards(cards) {
  const result = [];
  const seen = new Map();
  for (const card of cards.filter(Boolean)) {
    const keys = cardKeys(card);
    const existing = keys.map((key) => seen.get(key)).find(Boolean);
    if (existing) {
      existing.faces = normalizeCard({ ...existing, faces: [...existing.faces, ...card.faces] }).faces;
      existing.scryfall_uri ||= card.scryfall_uri;
      for (const key of keys) seen.set(key, existing);
    } else {
      result.push(card);
      for (const key of keys) seen.set(key, card);
    }
  }
  return result;
}

function displayParts(name) {
  return name.split(/\s+(?:\/\/|\+)\s+/);
}

function includeMissingPartners(cards, name) {
  const parts = displayParts(name);
  if (parts.length < 2) return cards;
  // Scryfall may use a canonical equivalent of an EDHREC display name (for
  // example Sophina for Chief Jim Hopper). A complete pair needs no guessed
  // third card just because those names differ.
  if (cards.length >= parts.length) return cards;
  const matches = parts.map((part) => cards.find((card) => {
    const names = [card.name, ...displayParts(card.name), ...card.faces.map((face) => face.name)];
    return names.some((candidate) => candidate.toLowerCase() === part.toLowerCase());
  }));
  // A full DFC name covers both parts with the same card. A partial partner
  // match covers only one, so keep the other name available for lazy lookup.
  if (!matches.some(Boolean)) return cards;
  return uniqueCards([
    ...parts.map((part, index) => matches[index] || { name: part, scryfall_uri: null, faces: [] }),
    ...cards.filter((card) => !matches.includes(card)),
  ]);
}

/** Return every commander card, retaining each card's own name, link and faces. */
export function getCommanderCards(row) {
  if (!row || typeof row !== "object") return [];
  const name = cleanName(row.commander_name || row.commander);
  const structured = asArray(row.commander_cards);
  if (structured.length) return includeMissingPartners(uniqueCards(structured.map((card) => normalizeCard(card, name))), name);

  const names = preferredArray(row.scryfall_card_names, row.partner_card_names).map(cleanName);
  const primaryImage = row.card_image_url || row.scryfall_image_url || row.image_url || row.commander_image_url;
  const images = [...preferredArray(row.partner_card_image_urls, row.partner_image_urls, row.card_image_urls)];
  if (primaryImage && !images.includes(primaryImage)) images.unshift(primaryImage);
  const primaryUri = row.scryfall_uri || row.commander_scryfall_uri;
  const uris = [...preferredArray(row.partner_scryfall_uris, row.scryfall_uris)];
  if (primaryUri && !uris.includes(primaryUri)) uris.unshift(primaryUri);
  const count = Math.max(names.length, images.length, uris.length, name ? 1 : 0);
  const parts = displayParts(name);

  return includeMissingPartners(uniqueCards(Array.from({ length: count }, (_, index) => {
    const cardName = names[index] || (count > 1 ? parts[index] : name) || name;
    return normalizeCard({
      name: cardName,
      scryfall_uri: uris[index] || null,
      faces: images[index] ? [{ name: cardName.split(" // ")[0], image_url: images[index] }] : [],
    });
  })), name);
}

// Scryfall asks clients to space requests by 50–100 ms. Hover lookups share a
// queue and cache, including failures, so repeated rows never create a burst.
function lookupScryfall(path) {
  if (!lookupCache.has(path)) {
    const request = scryfallQueue.then(async () => {
      const wait = Math.max(0, nextScryfallRequest - Date.now());
      if (wait) await new Promise((resolve) => setTimeout(resolve, wait));
      nextScryfallRequest = Date.now() + 110;
      try {
        const response = await fetch(`https://api.scryfall.com/cards/${path}`, {
          headers: { Accept: "application/json" },
          signal: AbortSignal.timeout(8000),
        });
        if (!response.ok) return null;
        const data = await response.json();
        return data && data.object !== "error" ? data : null;
      } catch {
        return null;
      }
    });
    // The queue covers HTTP only: resolving a meld result may enqueue another
    // request and must never wait on its own metadata-resolution promise.
    scryfallQueue = request.then(() => undefined, () => undefined);
    const resolved = request.then(async (data) => {
      if (!data) return null;
      const card = normalizeCard(data);
      const meldResult = data.layout === "meld"
        ? asArray(data.all_parts).find((part) => part?.component === "meld_result")
        : null;
      const resultId = scryfallId(meldResult?.id) || scryfallId(meldResult?.uri);
      if (resultId && resultId !== path && resultId !== data.id && resultId !== scryfallId(data.uri)) {
        const back = await lookupScryfall(resultId);
        if (back) return normalizeCard({ ...card, faces: [...card.faces, ...back.faces] });
      }
      return card;
    }).catch(() => null);
    lookupCache.set(path, resolved);
  }
  return lookupCache.get(path);
}

function lookupByName(name) {
  return name ? lookupScryfall(`named?exact=${encodeURIComponent(name)}`) : Promise.resolve(null);
}

async function lookupStandaloneCommander(name) {
  commanderIndexPromise ||= loadCommanderIndex().catch(() => []);
  const index = await commanderIndexPromise;
  const rows = Array.isArray(index) ? index : index?.rows || [];
  const normalized = name.toLowerCase();
  const row = rows.find((candidate) => {
    const display = cleanName(candidate.commander_name || candidate.commander);
    return displayParts(display).length === 1 && display.toLowerCase() === normalized;
  });
  if (!row) return null;
  if (getCommanderCards(row).some((card) => card.faces.length)) return row;
  if (!row.commander_slug) return null;
  const detail = await loadDetail(row.commander_slug);
  const detailRows = Array.isArray(detail) ? detail : detail?.rows || [detail];
  return detailRows.find((candidate) => getCommanderCards(candidate).some((card) => card.faces.length)) || null;
}

async function hydrateCard(card, allowLocalFallback = true) {
  const id = card.faces.map((face) => scryfallId(face.image_url)).find(Boolean)
    || scryfallId(card.scryfall_uri);
  const byId = id ? await lookupScryfall(id) : null;
  if (byId?.faces.length) return [byId];
  const byName = await lookupByName(card.name);
  if (byName?.faces.length) return [byName];

  // Some EDHREC names (e.g. Chief Jim Hopper) resolve locally to Scryfall's
  // canonical equivalent (Sophina). Reuse that singleton's metadata when an
  // exact API lookup cannot find the displayed partner name.
  if (allowLocalFallback && displayParts(card.name).length === 1) {
    const local = await lookupStandaloneCommander(card.name);
    if (local) {
      const localCards = getCommanderCards(local);
      if (asArray(local.commander_cards).length) return localCards;
      const hydrated = [];
      for (const localCard of localCards) hydrated.push(...await hydrateCard(localCard, false));
      return hydrated;
    }
  }

  // "//" can identify either two faces or two commanders. Try the full name
  // first; split only after that lookup fails, then deduplicate by card ID/URI.
  const parts = displayParts(card.name);
  if (parts.length > 1) {
    const splitCards = [];
    for (const part of parts) {
      splitCards.push(...await hydrateCard({ name: part, scryfall_uri: null, faces: [] }, allowLocalFallback));
    }
    if (splitCards.some((candidate) => candidate.faces.length)) return uniqueCards(splitCards);
  }
  return [card];
}

function loadDetail(slug) {
  if (!detailCache.has(slug)) {
    detailCache.set(slug, loadCommanderDetail(slug).catch(() => null));
  }
  return detailCache.get(slug);
}

async function resolveCommanderCards(row) {
  let source = row;
  let cards = getCommanderCards(source);
  if (!cards.some((card) => card.faces.length) && row.commander_slug) {
    const detail = await loadDetail(row.commander_slug);
    const detailRows = Array.isArray(detail) ? detail : detail?.rows || [detail];
    const enriched = detailRows.find((candidate) => getCommanderCards(candidate).some((card) => card.faces.length));
    if (enriched) {
      source = { ...row, ...enriched };
      cards = getCommanderCards(source);
    }
  }

  const hasStructuredCards = asArray(source.commander_cards).length > 0;
  const name = cleanName(source.commander_name || source.commander);
  if (displayParts(name).length > 1 && cards.length > 1 && cards.some((card) => !card.faces.length)) {
    // Partial enrichment might contain only a DFC's front name or only one
    // partner. Resolve the full display name before treating these as a pair.
    const fullCard = await lookupByName(name);
    if (fullCard?.faces.length) return [fullCard];
  }
  const hydrated = [];
  for (const card of cards) {
    // Legacy names alone cannot distinguish a normal card from a meld front.
    // Fetch metadata on hover even when a legacy name has no "//" suffix.
    if (card.faces.length && hasStructuredCards) hydrated.push(card);
    else hydrated.push(...await hydrateCard(card));
  }
  const completed = includeMissingPartners(uniqueCards(hydrated), name);
  const result = [];
  for (const card of completed) {
    // A lookup by the primary image may reveal a second partner that was
    // hidden by a legacy combined display name with no per-card names.
    if (!card.faces.length) result.push(...await hydrateCard(card));
    else result.push(card);
  }
  return uniqueCards(result);
}

/** Lazily fill legacy/missing metadata; callers should invoke this on hover/focus. */
export function loadCommanderCards(row) {
  const source = row || {};
  // Include metadata so a sparse row cannot hide richer data loaded later.
  const key = JSON.stringify([source.commander_slug, getCommanderCards(source), source.scryfall_card_names, source.commander_cards]);
  if (!previewCache.has(key)) {
    previewCache.set(key, resolveCommanderCards(source).catch(() => getCommanderCards(source)));
  }
  return previewCache.get(key);
}
