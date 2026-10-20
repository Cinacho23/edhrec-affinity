import { useEffect, useState } from "react";

import { loadCommanderBuildStyles } from "../lib/api";
import {
  formatBuildPoints,
  formatBuildShare,
  getBuildModel,
  getBuildModelOptions,
  getComparableProfilePairs,
  getDeckMixtureRows,
  getSupportedThemes,
  safeExternalUrl,
} from "../lib/buildStyleUtils";
import { formatDecimal, formatNumber } from "../lib/formatters";
import "./CommanderBuildStyles.css";

function SourceLink({ url, children }) {
  const href = safeExternalUrl(url);
  return href ? <a href={href} target="_blank" rel="noreferrer">{children} ↗</a> : null;
}

function PackageCard({ pkg, index, decks }) {
  const themes = getSupportedThemes(pkg);
  const representatives = (pkg.representative_deck_ids || []).map(
    (id) => decks.find((deck) => deck.id === id)
  ).filter(Boolean);
  return (
    <article className="build-package">
      <div className="build-package__header">
        <h4>Package {index + 1}</h4>
        <span>{formatNumber(pkg.member_count)} sampled decks</span>
      </div>
      <p className="build-small-label">Suggested themes</p>
      <div className="build-theme-list">
        {themes.length ? themes.map((theme) => (
          <span className="build-theme" key={theme.tag_slug || theme.tag_name}
            title={`Card-profile similarity: ${formatDecimal(theme.similarity)}. These are suggested labels, not observed deck tags.`}>
            {theme.tag_name || theme.tag_slug}
          </span>
        )) : <span className="muted">No supported theme label</span>}
      </div>
      <details className="build-evidence" open>
        <summary>Distinctive cards</summary>
        <div className="build-table-scroll" tabIndex="0" role="region" aria-label={`Package ${index + 1} card evidence`}>
          <table>
            <thead><tr><th scope="col">Card</th><th scope="col">In package decks</th><th scope="col">Other decks</th></tr></thead>
            <tbody>{(pkg.top_cards || []).map((card) => (
              <tr key={card.name}><th scope="row">{card.name}</th><td>{formatBuildShare(card.member_rate)}</td><td>{formatBuildShare(card.other_rate)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </details>
      {representatives.length ? <p className="build-source-links">
        Examples: {representatives.map((deck, deckIndex) => (
          <SourceLink key={deck.id} url={deck.source_url || deck.preview_url}>deck {deckIndex + 1}</SourceLink>
        ))}
      </p> : null}
    </article>
  );
}

function DeckMixtures({ model }) {
  const [selectedPackage, setSelectedPackage] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const threshold = model.membership_threshold ?? 0.2;
  const rows = getDeckMixtureRows(model, selectedPackage, threshold);
  const visibleRows = showAll ? rows : rows.slice(0, 15);
  return (
    <details className="build-evidence build-deck-mixtures">
      <summary>Inspect individual decks and their package mixtures</summary>
      <p className="muted">Weights show each deck’s relative mixture of learned card packages. A deck can combine any number of packages. Reported source tags are shown separately and do not train the model.</p>
      <label className="build-filter">
        <span>Decks to show</span>
        <select value={selectedPackage} onChange={(event) => { setSelectedPackage(event.target.value); setShowAll(false); }}>
          <option value="all">All sampled decks</option>
          {model.packages.map((pkg, index) => <option key={pkg.id ?? index} value={index}>Package {index + 1} members (≥ {formatBuildShare(threshold)})</option>)}
        </select>
      </label>
      <div className="build-table-scroll" tabIndex="0" role="region" aria-label="Individual deck package weights">
        <table>
          <caption>{formatNumber(rows.length)} sampled decks{showAll || rows.length <= 15 ? "" : "; showing the first 15"}</caption>
          <thead><tr><th scope="col">Deck sources</th>{model.packages.map((pkg, index) => <th scope="col" key={pkg.id ?? index}>Package {index + 1}</th>)}<th scope="col">Reported tags</th></tr></thead>
          <tbody>{visibleRows.map((deck) => <tr key={deck.id}>
            <th scope="row"><div className="build-source-links"><SourceLink url={deck.source_url}>{deck.source_url?.includes("archidekt") ? "Archidekt" : deck.source_url?.includes("moxfield") ? "Moxfield" : "Original deck"}</SourceLink><SourceLink url={deck.preview_url}>EDHREC preview</SourceLink></div></th>
            {model.packages.map((pkg, index) => <td key={pkg.id ?? index}><span className="build-weight"><span className="build-weight__bar" style={{ width: formatBuildShare(Math.min(1, Math.max(0, deck.memberships?.[index] || 0))) }} /> <span>{formatBuildShare(deck.memberships?.[index])}</span></span></td>)}
            <td>{deck.source_tags?.length ? deck.source_tags.join(", ") : <span className="muted">None reported</span>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {rows.length > 15 ? <button type="button" className="build-show-more" onClick={() => setShowAll(!showAll)}>{showAll ? "Show fewer decks" : `Show all ${formatNumber(rows.length)} decks`}</button> : null}
    </details>
  );
}

function DeckPackages({ analysis }) {
  const [selectedCount, setSelectedCount] = useState("auto");
  const model = getBuildModel(analysis, selectedCount);
  const options = getBuildModelOptions(analysis);
  const selection = analysis.model_selection;
  const packages = model.packages || [];
  return (
    <div className="build-view">
      <div className="build-view__header">
        <div><h3>Recurring card packages</h3><p className="muted">Learned from cardlists, including decks with no tags. Each package can suggest one, several, or no theme labels.</p></div>
        {options.length ? <label className="build-filter build-model-select"><span>Number of packages</span><select value={selectedCount} onChange={(event) => setSelectedCount(event.target.value)}>
          <option value="auto">Auto — {analysis.package_count ?? analysis.packages.length} suggested</option>
          {options.map((count) => <option key={count} value={count}>{count} {count === 1 ? "package" : "packages"}</option>)}
        </select></label> : null}
      </div>
      <div className="build-selection-note">
        <strong>{selection?.mode === "automatic" ? `Auto suggests ${analysis.package_count ?? analysis.packages.length} ${(analysis.package_count ?? analysis.packages.length) === 1 ? "package" : "packages"}` : `${packages.length} ${packages.length === 1 ? "package" : "packages"} in this analysis`}</strong>
        {selection?.reason ? <span>{selection.reason}</span> : <span>The package count depends on the available deck data.</span>}
        {selectedCount !== "auto" ? <span>Viewing {packages.length} {packages.length === 1 ? "package" : "packages"} as an exploratory alternative.</span> : null}
        {selection?.max_components ? <span>Tested counts from 1 to {selection.max_components}. Only supported alternatives are selectable; more deck data can support a different count.</span> : null}
      </div>
      {packages.length ? <>
        <div className="build-metrics">
          <div><strong>{formatNumber(packages.length)}</strong><span>card packages</span></div>
          <div><strong>{formatNumber(model.unique_decks)}</strong><span>unique sampled decks</span></div>
          <div><strong>{formatBuildShare(model.weighted_reconstruction_captured)}</strong><span>weighted card signal captured</span></div>
        </div>
        <p className="build-note">Package membership can overlap, so counts need not add up to the sample size. Card percentages describe this sample. Captured card signal measures model fit, not confidence or the share of decks explained.</p>
        <div className="build-package-grid">{packages.map((pkg, index) => <PackageCard key={pkg.id ?? index} pkg={pkg} index={index} decks={model.decks || []} />)}</div>
        <DeckMixtures key={selectedCount} model={model} />
      </> : <p className="muted">{analysis.reason || analysis.interpretation || "There are not enough usable decklists to discover card packages yet. Tag-profile comparisons are available in the other view."}</p>}
      {selection?.candidates?.length ? <details className="build-evidence"><summary>How the package count was chosen</summary><p className="muted">The model balances reconstruction on held-out decks, simplicity, support, and stability. This is an exploratory estimate of useful packages.</p>{selection.validation_note ? <p className="muted">{selection.validation_note}</p> : null}<div className="build-table-scroll" tabIndex="0" role="region" aria-label="Package count comparison"><table><thead><tr><th scope="col">Packages</th><th scope="col">Held-out error ↓</th><th scope="col">Result</th></tr></thead><tbody>{selection.candidates.map((candidate) => <tr key={candidate.components}><th scope="row">{candidate.components}</th><td>{formatDecimal(candidate.validation_error, 3)}</td><td>{candidate.selected ? "Suggested" : candidate.eligible ? "Supported alternative" : candidate.reason || "Insufficient support"}</td></tr>)}</tbody></table></div></details> : null}
    </div>
  );
}

function TagProfiles({ profiles }) {
  const pairs = getComparableProfilePairs(profiles);
  const [selectedPair, setSelectedPair] = useState("");
  const pairKey = (pair) => `${pair.left}|${pair.right}`;
  const pair = pairs.find((item) => pairKey(item) === selectedPair) || pairs[0];
  const names = new Map(profiles.profiles.map((profile) => [profile.tag_slug, profile.tag_name]));
  return <div className="build-view">
    <h3>Related tag profiles</h3>
    <p className="muted">Compare cards used more often in each tagged build than in this commander’s overall decks. Similar profiles suggest related card packages; they do not prove the same decks carry both tags.</p>
    <div className="build-profile-groups">{(profiles.groups || []).map((group, index) => <div key={index}><span className="build-small-label">Profile group {index + 1}</span><div className="build-theme-list">{group.map((slug) => <span className="build-theme" key={slug}>{names.get(slug) || slug}</span>)}</div></div>)}</div>
    <p className="build-note">Groups may share tags. A tag can also stand alone. Similarity threshold: {formatDecimal(profiles.threshold)}.</p>
    {pair ? <>
      <label className="build-filter"><span>Inspect a profile comparison</span><select value={pairKey(pair)} onChange={(event) => setSelectedPair(event.target.value)}>{pairs.map((item) => <option value={pairKey(item)} key={pairKey(item)}>{names.get(item.left)} + {names.get(item.right)} · {formatDecimal(item.similarity)}</option>)}</select></label>
      <div className="build-comparison-summary"><strong>{formatDecimal(pair.similarity)} distinctive-card similarity</strong><span>{formatNumber(pair.shared_observed_cards)} comparable observed cards</span><span>Raw card similarity: {formatDecimal(pair.raw_similarity)}</span></div>
      <div className="build-table-scroll" tabIndex="0" role="region" aria-label="Cards supporting the profile comparison"><table><caption>Shared distinctive cards · percentage points above commander baseline</caption><thead><tr><th scope="col">Card</th><th scope="col">{names.get(pair.left)}</th><th scope="col">{names.get(pair.right)}</th></tr></thead><tbody>{(pair.shared_signature_cards || []).map((card) => <tr key={card.name}><th scope="row">{card.name}</th><td>{formatBuildPoints(card.left_excess)}</td><td>{formatBuildPoints(card.right_excess)}</td></tr>)}</tbody></table>{!pair.shared_signature_cards?.length ? <p className="muted">No shared signature cards reported.</p> : null}</div>
    </> : <p className="muted">There are not enough comparable profiles to calculate a tag pair yet.</p>}
    <details className="build-evidence"><summary>Inspect all {profiles.profiles.length} tag signatures</summary><div className="build-profile-grid">{profiles.profiles.map((profile) => <article key={profile.tag_slug}><h4>{profile.tag_name}</h4><p className="muted">{formatNumber(profile.deck_count)} tagged decks · {formatNumber(profile.comparable_cards)} comparable cards</p><ol>{(profile.signature_cards || []).map((card) => <li key={card.name}><span>{card.name}</span><span>{formatBuildPoints(card.excess)}</span></li>)}</ol></article>)}</div></details>
  </div>;
}

function BuildReport({ report }) {
  const [view, setView] = useState(report.deck_analysis.packages.length ? "decks" : "profiles");
  const analyzed = report.deck_analysis.unique_decks || 0;
  return <section className="commander-build-styles panel" aria-labelledby="build-discovery-heading">
    <div className="build-heading"><div><p className="eyebrow">Build discovery <span className="build-pilot">Exploratory</span></p><h2 id="build-discovery-heading">How this commander gets built</h2></div>{report.generated_at ? <span className="muted">Updated {new Date(report.generated_at).toLocaleDateString("en-US", { timeZone: "America/Toronto" })}</span> : null}</div>
    <p className="build-intro">Themes can overlap or describe different builds. Explore related tag profiles and the recurring card packages found in individual decks.</p>
    <p className="build-coverage">Coverage: {formatNumber(report.profiles.profiles.length)} tag profiles and {formatNumber(analyzed)} unique sampled decks from {formatNumber(report.total_edhrec_decks)} decks listed by EDHREC. This sample is not every deck and does not establish how common a style is.</p>
    <div className="build-view-switch" role="group" aria-label="Build discovery views"><button type="button" aria-pressed={view === "decks"} onClick={() => setView("decks")}>Individual deck packages</button><button type="button" aria-pressed={view === "profiles"} onClick={() => setView("profiles")}>Tag profile similarities</button></div>
    {view === "decks" ? <DeckPackages analysis={report.deck_analysis} /> : <TagProfiles profiles={report.profiles} />}
    <details className="build-evidence build-method-note"><summary>Data and interpretation</summary><p>{report.collection?.sampling || "Card profiles and deck previews collected from EDHREC."}</p><p>Deck tags are optional metadata. The package model uses cards, so untagged decks and decks with one or many tags can all contribute. Suggested themes describe a package’s similarity to tag profiles; they are not confirmed labels on every member deck.</p><ul>{(report.limits || []).map((limit) => <li key={limit}>{limit}</li>)}</ul></details>
  </section>;
}

export default function CommanderBuildStyles({ commanderSlug }) {
  const [state, setState] = useState({ loading: true, report: null, error: null });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    loadCommanderBuildStyles(commanderSlug, { signal: controller.signal }).then(
      (report) => { if (active) setState({ loading: false, report, error: null }); },
      (error) => { if (active && error.name !== "AbortError") setState({ loading: false, report: null, error: error.message }); }
    );
    return () => { active = false; controller.abort(); };
  }, [commanderSlug]);

  if (state.loading) return <p className="muted" role="status">Loading build discovery…</p>;
  if (state.error) return <section className="commander-build-styles panel"><h2>Build discovery</h2><p className="muted">The build discovery report could not load. The commander’s tag table is still available.</p><details><summary>Report details</summary><p>{state.error}</p></details></section>;
  if (!state.report) return <section className="commander-build-styles panel"><h2>Build discovery</h2><p className="muted">No build discovery report has been collected for this commander yet. Reports use tag-specific card profiles and a sample of individual decklists.</p></section>;
  return <BuildReport report={state.report} />;
}
