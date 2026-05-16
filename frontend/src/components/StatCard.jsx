/*
  StatCard is a small reusable display block for homepage metrics.

  Example:
  <StatCard label="Unique commanders" value="6,328" />
*/

export default function StatCard({ label, value, helper }) {
  return (
    <article className="stat-card">
      <p className="stat-card__label">{label}</p>
      <p className="stat-card__value">{value}</p>

      {helper ? <p className="stat-card__helper">{helper}</p> : null}
    </article>
  );
}