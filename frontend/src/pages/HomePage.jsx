import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  loadAnalysisSummary,
  loadSiteManifest,
  loadTrendSummary,
} from "../lib/api";
import { formatNumber } from "../lib/formatters";

export default function HomePage() {
  const [state, setState] = useState({
    loading: true,
    error: null,
    manifest: null,
    analysis: null,
    trends: null,
  });

  useEffect(() => {
    async function loadData() {
      try {
        const [manifest, analysis, trends] = await Promise.all([
          loadSiteManifest(),
          loadAnalysisSummary(),
          loadTrendSummary(),
        ]);

        setState({
          loading: false,
          error: null,
          manifest,
          analysis,
          trends,
        });
      } catch (error) {
        setState({
          loading: false,
          error: error.message,
          manifest: null,
          analysis: null,
          trends: null,
        });
      }
    }

    loadData();
  }, []);

  if (state.loading) {
    return (
      <section className="page">
        <p className="muted">Loading homepage data…</p>
      </section>
    );
  }

  if (state.error) {
    return (
      <section className="page">
        <div className="status-box status-box--error">
          <h1>Could not load homepage data</h1>
          <p>
            Check that the sharded JSON files exist in{" "}
            <code>frontend/public/data/latest/</code>.
          </p>
          <pre>{state.error}</pre>
        </div>
      </section>
    );
  }

  const { manifest, analysis, trends } = state;

  return (
    <section className="page">
      <div className="hero">
        <p className="eyebrow">EDHREC Commander Tag Affinity Analysis</p>
        <h1>Find commanders that are unusually associated with specific tags.</h1>
        <p className="hero__text">
          This static website serves the complete dataset through sharded JSON files:
          commander detail files, tag detail files, and paginated leaderboard files.
        </p>

        <div className="button-row">
          <Link className="button" to="/commanders">
            Commander Search
          </Link>
          <Link className="button" to="/tags">
            Tag Explorer
          </Link>
          <Link className="button" to="/sets">
            Set Explorer
          </Link>
          <Link className="button" to="/leaderboard">
            Global Leaderboard
          </Link>
        </div>
      </div>

      <div className="stat-grid">
        <article className="stat-card">
          <span>Rows</span>
          <strong>{formatNumber(manifest?.total_rows ?? analysis?.affinity_row_count)}</strong>
        </article>

        <article className="stat-card">
          <span>Commanders</span>
          <strong>{formatNumber(manifest?.unique_commanders ?? analysis?.unique_commander_count)}</strong>
        </article>

        <article className="stat-card">
          <span>Tags</span>
          <strong>{formatNumber(manifest?.unique_tags ?? analysis?.unique_tag_count)}</strong>
        </article>

        <article className="stat-card">
          <span>Leaderboard pages</span>
          <strong>{formatNumber(manifest?.leaderboard_export?.page_count)}</strong>
        </article>
      </div>

      <section className="panel">
        <h2>Dataset status</h2>
        <dl className="summary-list">
          <div>
            <dt>Current snapshot</dt>
            <dd>{trends?.current_snapshot ?? "Unavailable"}</dd>
          </div>

          <div>
            <dt>Previous snapshot</dt>
            <dd>{trends?.previous_snapshot ?? "No previous snapshot yet"}</dd>
          </div>

          <div>
            <dt>Trend comparison</dt>
            <dd>
              {trends?.previous_snapshot_found
                ? "Available"
                : "Unavailable until second snapshot"}
            </dd>
          </div>
        </dl>
      </section>
    </section>
  );
}
