export function formatBuildShare(value, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

export function formatBuildPoints(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${(number * 100).toFixed(1)} pts`;
}

export function formatBuildLift(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${Number(value).toFixed(digits)}×`;
}

export function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function getBuildModel(analysis, selectedCount = "auto") {
  if (!analysis) return { packages: [], decks: [] };
  const alternative = selectedCount === "auto" ? null : analysis.alternative_models?.find(
    (model) => Number(model.components) === Number(selectedCount)
  );
  if (!alternative) return analysis;
  const weights = new Map((alternative.memberships || []).map((item) => [item.id, item.weights]));
  return {
    ...analysis,
    ...alternative,
    decks: (analysis.decks || []).map((deck) => ({ ...deck, memberships: weights.get(deck.id) || [] })),
  };
}

export function getBuildModelOptions(analysis) {
  return [...new Set((analysis?.alternative_models || [])
    .filter((model) => Number.isInteger(model.components) && model.components >= 1 && Array.isArray(model.packages))
    .map((model) => model.components))].sort((left, right) => left - right);
}

export function getSupportedThemes(pkg) {
  const seen = new Set();
  return (pkg.tag_suggestions || []).filter((theme) => {
    const key = theme.tag_slug || theme.tag_name;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function getComparableProfilePairs(profiles) {
  const known = new Set((profiles?.profiles || []).map((profile) => profile.tag_slug));
  return (profiles?.pairs || [])
    .filter((pair) => known.has(pair.left) && known.has(pair.right) && Number.isFinite(pair.similarity))
    .sort((left, right) => right.similarity - left.similarity);
}

export function getDeckMixtureRows(model, selectedPackage = "all", threshold = 0) {
  const index = selectedPackage === "all" ? null : Number(selectedPackage);
  const decks = model?.decks || [];
  if (index === null) return decks;
  return decks.filter((deck) => Number(deck.memberships?.[index] || 0) >= threshold)
    .toSorted((left, right) => Number(right.memberships?.[index] || 0) - Number(left.memberships?.[index] || 0));
}

const DECK_ROLE_ORDER = [
  "Commander", "Lands", "Fast Mana", "Ramp", "Card Advantage", "Wipe", "Removal",
  "Tutor", "Recursion", "Protection", "Leftover", "Card Draw", "Draw", "Board Wipes",
  "Tutors", "Tokens", "Counters", "Sacrifice", "Finishers", "Other", "Other Spells",
];

const BUILD_EVIDENCE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const BUILD_EVIDENCE_FUTURE_TOLERANCE_MS = 5 * 60 * 1000;

export function getExternalDeckRating(rating, now = Date.now()) {
  if (!rating || !["verified", "stale"].includes(rating.status)
      || !Number.isFinite(rating.playability_pct) || rating.playability_pct < 0 || rating.playability_pct > 100) return null;
  const checkedAt = typeof rating.checked_at === "string" ? Date.parse(rating.checked_at) : NaN;
  if (!Number.isFinite(checkedAt) || !Number.isFinite(now) || checkedAt > now + BUILD_EVIDENCE_FUTURE_TOLERANCE_MS) return null;
  const status = rating.status === "stale" || now - checkedAt >= BUILD_EVIDENCE_MAX_AGE_MS ? "stale" : "verified";
  const inTargetRange = rating.playability_pct >= 72.5 && rating.playability_pct <= 77.5;
  return { ...rating, status, inTargetRange, targetMet: status === "verified" && inTargetRange };
}

export function isBuildMetadataFresh(metadata, now = Date.now()) {
  const oldest = typeof metadata?.oldest_checked_at === "string" ? Date.parse(metadata.oldest_checked_at) : NaN;
  return !metadata?.stale_cached && Number.isFinite(oldest) && Number.isFinite(now)
    && oldest <= now + BUILD_EVIDENCE_FUTURE_TOLERANCE_MS && now - oldest < BUILD_EVIDENCE_MAX_AGE_MS;
}

export function getGeneratedDeckGroups(deck) {
  const commanders = new Set((deck?.commanders || []).map((card) => card.name));
  const groups = new Map();
  for (const card of deck?.cards || []) {
    if (!card?.name || !Number.isInteger(card.quantity) || card.quantity <= 0) continue;
    const tags = Array.isArray(card.tags) ? card.tags.filter((tag) => typeof tag === "string" && tag.trim()) : [];
    const explicitRole = card.role || card.primary_role;
    const role = commanders.has(card.name) ? "Commander" : explicitRole
      || tags.find((tag) => DECK_ROLE_ORDER.some((known) => known.toLowerCase() === tag.toLowerCase()))
      || "Other Spells";
    if (!groups.has(role)) groups.set(role, { name: role, count: 0, cards: [] });
    const group = groups.get(role);
    group.count += card.quantity;
    group.cards.push({ ...card, tags: [role] });
  }
  const order = (role) => {
    const index = DECK_ROLE_ORDER.findIndex((known) => known.toLowerCase() === role.toLowerCase());
    return index === -1 ? DECK_ROLE_ORDER.length : index;
  };
  return [...groups.values()].map((group) => ({
    ...group, cards: group.cards.toSorted((left, right) => left.name.localeCompare(right.name)),
  })).sort((left, right) => order(left.name) - order(right.name) || left.name.localeCompare(right.name));
}

export function getGeneratedDeckExport(deck) {
  return getGeneratedDeckGroups(deck).flatMap((group) => group.cards.map((card) =>
    `${card.quantity} ${card.name}${group.name === "Commander" ? " *CMDR*" : ""} #${group.name.replaceAll("#", "").replaceAll("\n", " ")}`,
  )).join("\n") + (deck?.cards?.length ? "\n" : "");
}

export function splitPrimerCardReferences(primer) {
  return String(primer || "").split(/(\[\[[^\]\n]+\]\])/g).filter(Boolean).map((text) => (
    text.startsWith("[[") && text.endsWith("]]")
      ? { type: "card", text: text.slice(2, -2) }
      : { type: "text", text }
  ));
}

export function generatedDeckFilename(name, extension = "txt") {
  const basename = String(name || "generated-deck").normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "generated-deck";
  return `${basename.slice(0, 120)}.${extension === "md" ? "md" : "txt"}`;
}
