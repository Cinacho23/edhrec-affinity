import "./CommanderColors.css";
import { normalizeColorIdentity } from "../lib/colorIdentity.js";

const COLOR_NAMES = {
  W: "White",
  U: "Blue",
  B: "Black",
  R: "Red",
  G: "Green",
  C: "Colorless",
};

export default function CommanderColors({ colorIdentity }) {
  const identity = normalizeColorIdentity(colorIdentity);
  const colors = identity === null ? null : (identity || "C").split("");

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
