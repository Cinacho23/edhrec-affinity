export default function BracketBadge({ bracketKey, label }) {
  const classSuffix = String(bracketKey || "1").replace("/", "-");

  return (
    <span className={`bracket-badge bracket-badge--${classSuffix}`}>
      {label || `Bracket ${bracketKey}`}
    </span>
  );
}

