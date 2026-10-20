export function formatBuildShare(value, digits = 0) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

export function formatBuildPoints(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${(number * 100).toFixed(1)} pts`;
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
