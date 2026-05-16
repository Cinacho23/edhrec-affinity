/*
  MethodologyPage is intentionally plain in Chat 9.

  Later, this can become a stronger documentation page with examples,
  caveats, equations, and maybe charts.
*/

export default function MethodologyPage() {
  return (
    <div className="page page--narrow">
      <section className="page-header">
        <p className="eyebrow">Methodology</p>
        <h1>How commander tag affinity is measured</h1>

        <p>
          This project does not simply rank the most popular commanders. It
          looks for commanders that are unusually associated with specific
          EDHREC tags compared with the normal behavior of that tag.
        </p>
      </section>

      <section className="method-section">
        <h2>1. Core unit of analysis</h2>

        <p>
          Each row represents one commander-tag pair. For example, one row might
          describe how many Jasmine Boreal of the Seven decks are associated
          with the Vanilla tag.
        </p>
      </section>

      <section className="method-section">
        <h2>2. Affinity percentage</h2>

        <p>The core percentage is:</p>

        <pre>
          <code>tag_affinity_pct = tag_decks / total_decks</code>
        </pre>

        <p>
          This means that a commander with 200 Vanilla-tagged decks out of 5,000
          total decks has a Vanilla affinity of 4%.
        </p>
      </section>

      <section className="method-section">
        <h2>3. Per-tag baseline</h2>

        <p>
          Each tag has its own mean and standard deviation. This matters because
          some tags are naturally broad and common, while others are narrow and
          rare.
        </p>

        <pre>
          <code>tag_mean_pct = mean(tag_affinity_pct) within one tag</code>
        </pre>

        <pre>
          <code>
            tag_std_pct = sample standard deviation(tag_affinity_pct) within one
            tag
          </code>
        </pre>
      </section>

      <section className="method-section">
        <h2>4. Z-score</h2>

        <p>The z-score is:</p>

        <pre>
          <code>z = (tag_affinity_pct - tag_mean_pct) / tag_std_pct</code>
        </pre>

        <p>
          A high positive z-score means the commander is unusually associated
          with that tag compared with other commanders that have the same tag.
        </p>
      </section>

      <section className="method-section">
        <h2>5. Ranking and percentile</h2>

        <p>
          Commanders are ranked within each tag by metrics such as z-score,
          affinity percentage, and tag deck count. Percentile is included as a
          more intuitive companion to z-score.
        </p>
      </section>

      <section className="method-section">
        <h2>6. Sample-size filters</h2>

        <p>
          The default leaderboard filters out very small rows using minimum
          total decks and minimum tag decks. This reduces the chance that a tiny
          sample creates a misleading extreme result.
        </p>

        <ul>
          <li>Default minimum total decks: 200</li>
          <li>Default minimum tag decks: 5</li>
        </ul>
      </section>

      <section className="method-section">
        <h2>7. cEDH handling</h2>

        <p>
          cEDH is represented as a normalized tag in the processed table, but it
          comes from a special filtered source route rather than the normal
          commander tag list. The source type remains available for auditing.
        </p>
      </section>

      <section className="method-section">
        <h2>8. Trend metrics</h2>

        <p>
          Trend fields compare the current processed snapshot against the
          previous processed snapshot. Because the current prototype has only
          one real snapshot, trend values are unavailable until the next
          complete scrape-clean-analysis cycle.
        </p>
      </section>

      <section className="method-section">
        <h2>9. Responsible data use</h2>

        <p>
          This project should not function as a replacement for EDHREC. The goal
          is original statistical analysis, not direct republication of EDHREC’s
          browsing experience. The site should link back to EDHREC where
          appropriate and explain its limitations clearly.
        </p>
      </section>
    </div>
  );
}