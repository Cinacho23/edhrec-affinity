import "./CommanderColors.css";

const COLOR_NAMES = {
  W: "White",
  U: "Blue",
  B: "Black",
  R: "Red",
  G: "Green",
  C: "Colorless",
};
const COLOR_ORDER = ["W", "U", "B", "R", "G"];

function getColors(value) {
  if (value == null) return null;
  const text = (Array.isArray(value) ? value.join("") : String(value))
    .trim()
    .toUpperCase();

  if ((Array.isArray(value) && value.length === 0) || text === "C" || text === "COLORLESS") {
    return ["C"];
  }

  // Accept arrays, compact WUBRG codes, and legacy comma-separated codes.
  const codes = text.replace(/[\s,'"[\]{}]/g, "");
  if (!codes || !/^[WUBRG]+$/.test(codes)) return null;
  return COLOR_ORDER.filter((color) => codes.includes(color));
}

export default function CommanderColors({ colorIdentity }) {
  const colors = getColors(colorIdentity);

  if (!colors) {
    return <span className="commander-colors commander-colors--unknown" aria-label="Colors unknown" title="Colors unknown">—</span>;
  }

  const label = colors.map((color) => COLOR_NAMES[color]).join(", ");

  return (
    <span className="commander-colors" role="img" aria-label={label} title={label}>
      {colors.map((color) => (
        <span key={color} className={`commander-colors__pip commander-colors__pip--${color.toLowerCase()}`} aria-hidden="true">
          {color}
        </span>
      ))}
    </span>
  );
}
