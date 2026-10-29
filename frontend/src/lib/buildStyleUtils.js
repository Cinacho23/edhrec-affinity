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

export function getBuildThemeEvidence(theme) {
  const tier = ["main", "supporting"].includes(theme?.support_tier) ? theme.support_tier : null;
  const deckCount = Number.isSafeInteger(theme?.deck_count) && theme.deck_count >= 0 ? theme.deck_count : null;
  const commanderDeckCount = Number.isSafeInteger(theme?.commander_deck_count) && theme.commander_deck_count > 0
    ? theme.commander_deck_count : null;
  // A report's analyzed sample is not the population used for tag prevalence.
  // Older reports keep their counts until explicit population evidence exists.
  const fraction = deckCount !== null && commanderDeckCount !== null && deckCount <= commanderDeckCount
      && Number.isFinite(theme?.deck_fraction) && theme.deck_fraction >= 0 && theme.deck_fraction <= 1
      && Math.abs(theme.deck_fraction - deckCount / commanderDeckCount) <= 0.00001
    ? theme.deck_fraction : null;
  const tierLabel = tier === "main" ? "Main" : tier === "supporting" ? "Supporting" : "";
  const countLabel = deckCount === null ? "" : `${deckCount.toLocaleString("en-US")} tag ${deckCount === 1 ? "deck" : "decks"}`;
  const shareLabel = fraction === null ? "" : fraction.toLocaleString("en-US", { style: "percent", maximumFractionDigits: 1 });
  const similarity = Number.isFinite(theme?.similarity) ? ` Card-profile similarity: ${theme.similarity.toFixed(2)}.` : "";
  return {
    tier,
    note: [tierLabel, shareLabel || countLabel].filter(Boolean).join(" · "),
    tooltip: `${tierLabel ? `${tierLabel} tag. ` : ""}${deckCount === null
      ? "Tag-profile deck count unavailable."
      : fraction === null ? `EDHREC lists ${countLabel} for this commander. Commander-wide tag percentage unavailable.`
        : `EDHREC lists ${countLabel} out of ${commanderDeckCount.toLocaleString("en-US")} total decks for this commander (${shareLabel}). Tiers use reported tag prevalence and minimum deck-count evidence, not model confidence or package membership.`}${tier === "supporting"
      ? " Supporting matches may have lower prevalence or less source evidence; treat the suggested label as tentative." : ""}${similarity} Suggested labels describe card-profile matches, not confirmed tags on every analyzed deck. Tags can overlap, so percentages need not sum to 100%.`,
  };
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

const BUILD_EVIDENCE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const BUILD_EVIDENCE_FUTURE_TOLERANCE_MS = 5 * 60 * 1000;

const BUILD_SOURCE_TARGET_HOURS = 96;

export function hasBuildStylePopulation(report, minimum = 200) {
  return Number.isSafeInteger(minimum) && minimum >= 200 && Number.isSafeInteger(report?.total_edhrec_decks)
    && report.total_edhrec_decks >= minimum;
}

export function getCurrentBuildDiscoveryCommanders(index) {
  if (index?.discovery?.policy !== "current_collector" || !Array.isArray(index.discovery.commander_slugs)
      || !Array.isArray(index.commanders) || !Number.isSafeInteger(index.discovery.min_total_decks)
      || index.discovery.min_total_decks < 200) return [];
  const minimum = index.discovery.min_total_decks;
  const approved = new Set(index.discovery.commander_slugs.filter((slug) => typeof slug === "string" && slug.trim()));
  const seen = new Set();
  return index.commanders.filter((commander) => {
    const slug = commander?.commander_slug;
    if (!approved.has(slug) || seen.has(slug) || !hasBuildStylePopulation(commander, minimum)) return false;
    seen.add(slug);
    return true;
  });
}

function observedTimestamp(value, now) {
  const timestamp = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(timestamp) && timestamp <= now + BUILD_EVIDENCE_FUTURE_TOLERANCE_MS ? timestamp : NaN;
}

export function getBuildSourceFreshness(collection, now = Date.now()) {
  const evidence = collection?.freshness;
  const targetHours = BUILD_SOURCE_TARGET_HOURS;
  const targetAge = targetHours * 60 * 60 * 1000;
  const sourceOldest = observedTimestamp(evidence?.oldest_source_at, now);
  const sourceNewest = observedTimestamp(evidence?.newest_source_at, now);
  const versionsVerified = evidence?.basis === "current_index_versions";
  const oldest = versionsVerified ? observedTimestamp(evidence?.oldest_verified_at, now) : sourceOldest;
  const newest = versionsVerified ? observedTimestamp(evidence?.newest_verified_at, now) : sourceNewest;
  const dated = evidence?.dated_previews;
  const undated = evidence?.undated_previews;
  const observed = evidence?.observed_previews;
  const stale = evidence?.stale_previews;
  const countsKnown = [dated, undated, observed, stale].every((value) => Number.isInteger(value) && value >= 0)
    && dated + undated === observed && stale <= observed;
  const result = { status: "unknown", label: "Collected source age unknown", targetHours, versionsVerified,
    oldestSourceAt: Number.isFinite(sourceOldest) ? evidence.oldest_source_at : null,
    newestSourceAt: Number.isFinite(sourceNewest) ? evidence.newest_source_at : null,
    oldestVerifiedAt: versionsVerified && Number.isFinite(oldest) ? evidence.oldest_verified_at : null,
    newestVerifiedAt: versionsVerified && Number.isFinite(newest) ? evidence.newest_verified_at : null };
  if (collection?.frame_decks === 0 && collection.complete === true && evidence?.observed_previews === 0) {
    return { ...result, status: "fresh", label: "No deck previews listed" };
  }
  if (!Number.isFinite(now) || !countsKnown || !observed || !dated || !Number.isFinite(oldest)
      || !Number.isFinite(newest) || oldest > newest) return result;
  if (now - newest >= targetAge && undated === 0) {
    return { ...result, status: "stale", label: versionsVerified ? "Collected verification checks need refresh" : "Collected sources need refresh" };
  }
  if (now - oldest >= targetAge || undated > 0) {
    return { ...result, status: "mixed", label: versionsVerified ? "Mixed or incomplete verification ages" : "Mixed or incomplete source ages" };
  }
  if (stale > 0) {
    return { ...result, status: "mixed", label: versionsVerified ? "Collected deck versions need verification" : "Collected sources need a refresh" };
  }
  return { ...result, status: "fresh", label: versionsVerified ? "Collected deck versions verified recently" : "Collected sources checked recently" };
}

export function getBuildCollectionProgress(collection, now = Date.now()) {
  const frame = collection?.frame_decks;
  const attempted = collection?.previews_attempted;
  const known = Number.isInteger(frame) && frame >= 0 && Number.isInteger(attempted) && attempted >= 0 && attempted <= frame;
  const complete = known && collection?.target_mode === "all" && collection.complete === true && attempted >= frame;
  const freshness = getBuildSourceFreshness(collection, now);
  const indexAt = observedTimestamp(collection?.freshness?.index_source_at, now);
  const indexFresh = Number.isFinite(indexAt) && now - indexAt < freshness.targetHours * 60 * 60 * 1000
    && collection?.freshness?.index_stale === false;
  const analysisCurrent = collection?.analysis_current !== false;
  const unavailable = Number.isInteger(collection?.unavailable_previews) && collection.unavailable_previews >= 0
    ? collection.unavailable_previews : null;
  return {
    known, frame, attempted, complete, indexFresh, analysisCurrent, unavailable,
    indexSourceAt: Number.isFinite(indexAt) ? collection.freshness.index_source_at : null,
    targetMet: complete && freshness.status === "fresh" && indexFresh && analysisCurrent && unavailable === 0,
    label: complete ? "Full index checked" : known && attempted === frame && collection?.target_mode === "all"
      ? "Full index awaiting refresh" : known ? "Partial index coverage" : "Index coverage unknown",
  };
}

const POWER_SCORE_BRACKET_THRESHOLDS = [4.7, 6.7, 7.7, 9.25];

export function bracketForBuildPowerScore(value) {
  if (typeof value === "string") {
    value = value.trim();
    if (!/^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(value)) return null;
  } else if (typeof value !== "number") return null;
  const score = Number(value);
  if (!Number.isFinite(score) || score < 0) return null;
  return 1 + POWER_SCORE_BRACKET_THRESHOLDS.filter((threshold) => score >= threshold).length;
}

export function getExternalDeckRating(rating, now = Date.now(), expectedHash = null) {
  if (!rating || !["verified", "stale"].includes(rating.status)) return null;
  if (expectedHash && rating.deck_sha256 !== expectedHash) return null;
  const scoreBracket = bracketForBuildPowerScore(rating.power_level);
  const playabilityKnown = Number.isFinite(rating.playability_pct)
    && rating.playability_pct >= 0 && rating.playability_pct <= 100;
  if (!playabilityKnown && scoreBracket === null) return null;
  const checkedAt = typeof rating.checked_at === "string" ? Date.parse(rating.checked_at) : NaN;
  if (!Number.isFinite(checkedAt) || !Number.isFinite(now) || checkedAt > now + BUILD_EVIDENCE_FUTURE_TOLERANCE_MS) return null;
  const status = rating.status === "stale" || now - checkedAt >= BUILD_EVIDENCE_MAX_AGE_MS ? "stale" : "verified";
  const reportedBracket = Number.isInteger(rating.bracket) && rating.bracket >= 1 && rating.bracket <= 5 ? rating.bracket : null;
  const inTargetRange = playabilityKnown && rating.playability_pct >= 72.5 && rating.playability_pct <= 77.5;
  return { ...rating, status, playabilityKnown, scoreBracket, reportedBracket,
    bracketMismatch: scoreBracket !== null && reportedBracket !== null && scoreBracket !== reportedBracket,
    inTargetRange, targetMet: status === "verified" && inTargetRange };
}

export function getGeneratedDeckPowerRating(deck, now = Date.now()) {
  if (deck?.status !== "generated") return null;
  const validation = deck.validation || {};
  const external = getExternalDeckRating(validation.external_rating, now, validation.deck_sha256);
  if (external?.status === "verified" && external.scoreBracket !== null) {
    return { ...external, kind: "external", label: "calculated power" };
  }
  const calculated = validation.calculated_power;
  if (calculated?.status !== "calculated" || calculated.source !== "local_public_formula"
      || calculated.power_formula_version !== 1 || !/^[a-f0-9]{64}$/.test(validation.deck_sha256 || "")
      || calculated.deck_sha256 !== validation.deck_sha256) return null;
  const scoreBracket = bracketForBuildPowerScore(calculated.power_level);
  const checkedAt = typeof calculated.checked_at === "string" ? Date.parse(calculated.checked_at) : NaN;
  if (scoreBracket === null || !Number.isFinite(now) || !Number.isFinite(checkedAt)
      || checkedAt > now + BUILD_EVIDENCE_FUTURE_TOLERANCE_MS || now - checkedAt >= BUILD_EVIDENCE_MAX_AGE_MS) return null;
  return { ...calculated, scoreBracket, kind: "local", label: "local power estimate" };
}

export function getPackageBracketLabel(deck, now = Date.now()) {
  if (deck?.status !== "generated") return null;
  const rating = getGeneratedDeckPowerRating(deck, now);
  if (!rating) return "Power bracket unverified";
  const power = Number(rating.power_level);
  const rounded = power.toFixed(2);
  const displayPower = bracketForBuildPowerScore(rounded) === rating.scoreBracket ? rounded : String(power);
  return `Power ${displayPower} · Bracket ${rating.scoreBracket} · ${rating.kind === "external" ? "EDHPowerLevel" : "local estimate"}`;
}

export function getGeneratedDeckPowerAlignment(deck, now = Date.now()) {
  const alignment = deck?.validation?.power_alignment;
  if (!["pending", "known_mismatch"].includes(alignment?.status)) return null;
  const rating = getGeneratedDeckPowerRating(deck, now);
  if (!rating) return null;
  const minimum = Number.isInteger(alignment.minimum_checked_bracket)
    && alignment.minimum_checked_bracket >= 1 && alignment.minimum_checked_bracket <= 5
    ? alignment.minimum_checked_bracket : null;
  return { status: minimum !== null && minimum > rating.scoreBracket ? "known_mismatch" : "pending",
    scoreBracket: rating.scoreBracket, minimumCheckedBracket: minimum };
}

export function getGeneratedDeckDistinctiveCards(deck) {
  const evidence = deck?.distinctive_cards;
  if (deck?.status !== "generated" || !Number.isInteger(deck.generation_version) || deck.generation_version < 2
      || !Number.isInteger(evidence?.total) || evidence.total < 0
      || !Number.isInteger(evidence.included_count) || evidence.included_count < 0
      || !Array.isArray(evidence.included) || !Array.isArray(evidence.omitted)
      || evidence.included_count !== evidence.included.length
      || evidence.total !== evidence.included_count + evidence.omitted.length
      || [...evidence.included, ...evidence.omitted].some((card) => typeof card?.name !== "string" || !card.name.trim())) return null;
  return {
    total: evidence.total,
    includedCount: evidence.included_count,
    omitted: evidence.omitted.map((card) => ({
      ...card,
      reason: typeof card.reason === "string" && card.reason.trim()
        ? card.reason : "The saved report does not include an omission explanation.",
    })),
  };
}

export function getGeneratedDeckBracketRepair(deck) {
  const repair = deck?.statistics?.score_bracket_repair;
  if (deck?.status !== "generated" || !["repaired", "retained", "blocked"].includes(repair?.status)
      || !Number.isInteger(repair.score_bracket) || repair.score_bracket < 1 || repair.score_bracket > 5) return null;
  const swaps = (Array.isArray(repair.swap_details) ? repair.swap_details : [])
    .filter((swap) => typeof swap?.out === "string" && swap.out.trim()
      && typeof swap?.in === "string" && swap.in.trim());
  return {
    status: repair.status,
    targetBracket: repair.score_bracket,
    initialBracket: bracketForBuildPowerScore(repair.from_power_level)
      ?? (Number.isInteger(repair.initial_score_bracket) && repair.initial_score_bracket >= 1
        && repair.initial_score_bracket <= 5 ? repair.initial_score_bracket : repair.score_bracket),
    previousBracket: Number.isInteger(repair.from_bracket) ? repair.from_bracket : null,
    previousPower: typeof repair.from_power_level === "number" && Number.isFinite(repair.from_power_level)
      || typeof repair.from_power_level === "string" && repair.from_power_level.trim()
      ? String(repair.from_power_level) : null,
    swaps,
    reason: typeof repair.reason === "string" && repair.reason.trim() ? repair.reason : null,
  };
}

export function getGeneratedDeckColorChoices(deck) {
  const names = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" };
  const choices = deck?.validation?.commander_color_choices;
  if (deck?.status !== "generated" || !Array.isArray(choices) || !Array.isArray(deck.commanders)) return [];
  const commanders = new Set(deck.commanders.map((card) => card?.name).filter((name) => typeof name === "string" && name.trim()));
  const seen = new Set();
  for (const choice of choices) {
    if (!commanders.has(choice?.commander_name) || !Object.hasOwn(names, choice?.color)
        || seen.has(choice.commander_name)) return [];
    seen.add(choice.commander_name);
  }
  return choices.map((choice) => ({ commanderName: choice.commander_name, colorName: names[choice.color] }));
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
    const isCommander = commanders.has(card.name) || card.primary_role === "Commander"
      || card.role === "Commander" || (Array.isArray(card.tags) && card.tags.includes("Commander"));
    const section = isCommander ? "Commander" : "Main deck";
    if (!groups.has(section)) groups.set(section, { name: section, count: 0, cards: [] });
    const group = groups.get(section);
    group.count += card.quantity;
    const untagged = { ...card };
    delete untagged.tags;
    group.cards.push(untagged);
  }
  return [...groups.values()].map((group) => ({
    ...group, cards: group.cards.toSorted((left, right) => left.name.localeCompare(right.name)),
  })).sort((left, right) => Number(right.name === "Commander") - Number(left.name === "Commander"));
}

export function getGeneratedDeckExport(deck) {
  const lines = getGeneratedDeckGroups(deck).flatMap((group) => group.cards.map((card) =>
    `${card.quantity} ${card.name}${group.name === "Commander" ? " *CMDR*" : ""}`,
  ));
  return lines.join("\n") + (lines.length ? "\n" : "");
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
