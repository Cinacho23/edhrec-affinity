import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { loadBuildStyleIndex } from "../lib/api";
import { formatNumber } from "../lib/formatters";
import "./BuildDiscoveryPage.css";

function reportDate(value) {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/Toronto" })
    : "Date unavailable";
}

export default function BuildDiscoveryPage() {
  const [state, setState] = useState({ loading: true, commanders: [], index: null, error: null });
  const [query, setQuery] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    loadBuildStyleIndex({ signal: controller.signal }).then(
      (index) => { if (active) setState({ loading: false, commanders: index.commanders, index, error: null }); },
      (error) => { if (active && error.name !== "AbortError") setState({ loading: false, commanders: [], error: error.message }); }
    );
    return () => { active = false; controller.abort(); };
  }, []);

  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return state.commanders.filter((commander) => String(commander.commander_name || commander.commander_slug).toLowerCase().includes(normalized))
      .toSorted((left, right) => String(left.commander_name || left.commander_slug).localeCompare(String(right.commander_name || right.commander_slug)));
  }, [state.commanders, query]);

  const totalCatalog = Number.isInteger(state.index?.total_catalog) && state.index.total_catalog >= state.commanders.length
    ? state.index.total_catalog : null;
  const pending = totalCatalog === null ? null : totalCatalog - state.commanders.length;

  return <section className="page build-discovery-page">
    <div className="page-header">
      <p className="eyebrow">Exploratory analysis</p>
      <h1>Build discovery</h1>
      <p>Explore the different ways a commander gets built. Compare tag-specific card profiles and recurring card packages learned from individual decklists, including decks with no tags.</p>
    </div>

    <div className="panel build-discovery-coverage">
      <h2>Available commander reports</h2>
      <p>Each report covers sampled decks for one commander. The package count adapts to the evidence, and a package can suggest one or several themes. These reports describe a sample rather than every EDHREC deck.</p>
      {!state.loading && !state.error ? <>
        <strong>{totalCatalog === null
          ? `${formatNumber(state.commanders.length)} ${state.commanders.length === 1 ? "commander report available" : "commander reports available"}`
          : `${formatNumber(state.commanders.length)} of ${formatNumber(totalCatalog)} commanders analyzed`}</strong>
        {pending !== null ? <p className="build-discovery-coverage__pending">{formatNumber(pending)} {pending === 1 ? "commander remains" : "commanders remain"} pending in this dataset. Analyzed commanders stay listed when there is insufficient data to fit packages.</p> : null}
      </> : null}
    </div>

    {state.loading ? <p className="muted" role="status">Loading available build reports…</p> : state.error ? <div className="panel"><p className="error-message">Could not load build discovery: {state.error}</p></div> : state.commanders.length ? <>
      <div className="search-panel build-discovery-search">
        <label htmlFor="build-discovery-search">Search available commanders</label>
        <input id="build-discovery-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search commander name…" />
        <p className="muted" aria-live="polite">{formatNumber(visible.length)} of {formatNumber(state.commanders.length)} reports</p>
      </div>
      <div className="build-discovery-grid">{visible.map((commander) => <article className="panel build-discovery-card" key={commander.commander_slug}>
        <h2><Link to={`/commanders/${encodeURIComponent(commander.commander_slug)}`}>{commander.commander_name || commander.commander_slug}</Link></h2>
        <div className="build-discovery-card__stats">
          <div><strong>{formatNumber(commander.package_count)}</strong><span>{commander.package_count === 1 ? "suggested package" : "suggested packages"}</span></div>
          <div><strong>{formatNumber(commander.profile_count)}</strong><span>tag profiles</span></div>
          <div><strong>{formatNumber(commander.unique_decks)}</strong><span>sampled decks</span></div>
        </div>
        {commander.status !== "fitted" ? <p className="muted">{commander.profile_count > 0
          ? "Tag profile comparisons available; deck evidence is insufficient to fit packages."
          : "Insufficient data for tag comparisons or packages."}</p> : null}
        <div className="build-discovery-card__footer"><span>Analyzed {reportDate(commander.generated_at)}</span><Link to={`/commanders/${encodeURIComponent(commander.commander_slug)}`}>Explore report →</Link></div>
      </article>)}</div>
      {!visible.length ? <p className="muted">No available reports match this search.</p> : null}
    </> : <div className="panel"><p className="muted">No build discovery reports are available in this dataset yet.</p><Link className="build-discovery-all-commanders" to="/commanders">Browse all commanders →</Link></div>}
  </section>;
}
