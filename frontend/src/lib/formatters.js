export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return new Intl.NumberFormat("en-US").format(Number(value));
}

export function formatDecimal(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return Number(value).toFixed(digits);
}

export function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const number = Number(value);

  // Backend may provide either decimal 0.037 or display value 3.7.
  const displayValue = Math.abs(number) <= 1 ? number * 100 : number;

  return `${displayValue.toFixed(digits)}%`;
}

export function formatSigned(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  const number = Number(value);
  const sign = number > 0 ? "+" : "";

  return `${sign}${number.toFixed(digits)}`;
}

const COLOR_ORDER = ["W", "U", "B", "R", "G"];

const COLOR_IDENTITY_NAMES = {
  W: "White",
  U: "Blue",
  B: "Black",
  R: "Red",
  G: "Green",
  WU: "Azorius",
  WB: "Orzhov",
  WR: "Boros",
  WG: "Selesnya",
  UB: "Dimir",
  UR: "Izzet",
  UG: "Simic",
  BR: "Rakdos",
  BG: "Golgari",
  RG: "Gruul",
  WUB: "Esper",
  WUR: "Jeskai",
  WUG: "Bant",
  WBR: "Mardu",
  WBG: "Abzan",
  WRG: "Naya",
  UBR: "Grixis",
  UBG: "Sultai",
  URG: "Temur",
  BRG: "Jund",
  WUBR: "Four-colour, no Green",
  WUBG: "Four-colour, no Red",
  WURG: "Four-colour, no Black",
  WBRG: "Four-colour, no Blue",
  UBRG: "Four-colour, no White",
  WUBRG: "Five-colour",
};

function normalizeColorIdentity(value) {
  if (!value) return [];

  if (Array.isArray(value)) {
    return value
      .map((color) => String(color).trim().toUpperCase())
      .filter((color) => COLOR_ORDER.includes(color))
      .sort((a, b) => COLOR_ORDER.indexOf(a) - COLOR_ORDER.indexOf(b));
  }

  const text = String(value).trim().toUpperCase();

  if (!text || text === "COLORLESS") {
    return [];
  }

  const colors = text.includes(",")
    ? text.split(",").map((part) => part.trim())
    : text.split("");

  return colors
    .filter((color) => COLOR_ORDER.includes(color))
    .sort((a, b) => COLOR_ORDER.indexOf(a) - COLOR_ORDER.indexOf(b));
}

export function formatColorIdentity(value) {
  const colors = normalizeColorIdentity(value);

  if (colors.length === 0) {
    return "Colorless";
  }

  const code = colors.join("");
  const identityName = COLOR_IDENTITY_NAMES[code] || `${colors.length}-colour`;

  return `${code} (${identityName})`;
}

export function formatRank(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }

  return `#${Number(value).toLocaleString("en-US")}`;
}
