/*
  TagSummaryCard.jsx

  A compact summary card for the selected tag on the Tag Explorer page.

  It reads fields defensively because the exact names can evolve slightly as
  the backend improves. This keeps the frontend from breaking if a summary
  field is renamed later.
*/

import {
  formatDecimal,
  formatNumber,
  formatPercentFromDecimal,
  getNumber,
} from "../lib/filterUtils";

export default function TagSummaryCard({ tagSummary, selectedTagName }) {
  if (!tagSummary) {
    return (
      <section className="summary-card">
        <p className="eyebrow">Selected tag</p>
        <h2>{selectedTagName || "No tag selected"}</h2>
        <p>Select a tag to view its summary.</p>
      </section>
    );
  }

  const rowCount =
    getNumber(tagSummary.row_count) ??
    getNumber(tagSummary.tag_row_count) ??
    getNumber(tagSummary.total_row_count);

  const eligibleCount =
    getNumber(tagSummary.analysis_eligible_row_count) ??
    getNumber(tagSummary.eligible_row_count);

  const meanPct =
    getNumber(tagSummary.tag_mean_pct) ??
    getNumber(tagSummary.mean_pct) ??
    getNumber(tagSummary.avg_affinity_pct);

  const stdPct =
    getNumber(tagSummary.tag_std_pct) ??
    getNumber(tagSummary.std_pct) ??
    getNumber(tagSummary.affinity_std_pct);

  const maxZ =
    getNumber(tagSummary.max_z) ??
    getNumber(tagSummary.max_z_score) ??
    getNumber(tagSummary.highest_z);

  return (
    <section className="summary-card">
      <p className="eyebrow">Selected tag</p>
      <h2>{tagSummary.tag_name ?? selectedTagName ?? "Unknown tag"}</h2>

      <div className="summary-grid">
        <div>
          <span>Total rows</span>
          <strong>{formatNumber(rowCount)}</strong>
        </div>

        <div>
          <span>Eligible rows</span>
          <strong>{formatNumber(eligibleCount)}</strong>
        </div>

        <div>
          <span>Mean affinity</span>
          <strong>{formatPercentFromDecimal(meanPct)}</strong>
        </div>

        <div>
          <span>Std. dev.</span>
          <strong>{formatPercentFromDecimal(stdPct)}</strong>
        </div>

        <div>
          <span>Max z-score</span>
          <strong>{formatDecimal(maxZ, 2)}</strong>
        </div>
      </div>
    </section>
  );
}