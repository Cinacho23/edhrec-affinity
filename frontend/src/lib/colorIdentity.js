const COLORS = ["W", "U", "B", "R", "G"];

// Increasing colour count, rotating clockwise from white within each group.
// Allied pairs/shards precede enemy pairs/wedges. Displayed pip order is
// independent of this identity order (for example, WG and GW are both Selesnya).
export const COLOR_IDENTITY_ORDER = Object.freeze([
  "C",
  "W", "U", "B", "R", "G",
  "WU", "UB", "BR", "RG", "GW",
  "WB", "UR", "BG", "RW", "GU",
  "WUB", "UBR", "BRG", "RGW", "GWU",
  "WBG", "URW", "BGU", "RWB", "GUR",
  "WUBR", "UBRG", "BRGW", "RGWU", "GWUB",
  "WUBRG",
]);

// An empty identity is colourless; null means missing or invalid data.
export function normalizeColorIdentity(value) {
  if (value == null) return null;
  if (Array.isArray(value) && value.length === 0) return "";
  if (typeof value !== "string" && !Array.isArray(value)) return null;

  const text = (Array.isArray(value) ? value.join("") : value).trim().toUpperCase();
  if (["C", "COLORLESS", "COLOURLESS", "[]"].includes(text)) return "";

  // Arrays, compact codes and legacy comma-separated/serialized codes all
  // describe the same unordered set. Do not interpret an unknown blank as C.
  const codes = text.replace(/[\s,'"[\]{}]/g, "");
  if (!codes || !/^[WUBRG]+$/.test(codes)) return null;
  return COLORS.filter((color) => codes.includes(color)).join("");
}

const COLOR_RANKS = new Map(COLOR_IDENTITY_ORDER.map((identity, rank) => [
  normalizeColorIdentity(identity), rank,
]));

export function getColorIdentityRank(value) {
  const identity = normalizeColorIdentity(value);
  return identity === null ? null : COLOR_RANKS.get(identity) ?? null;
}
