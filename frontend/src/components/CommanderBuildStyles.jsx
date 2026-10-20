import { useEffect, useState } from "react";

import { loadCommanderBuildStyles } from "../lib/api";
import {
  formatBuildPoints,
  formatBuildLift,
  formatBuildShare,
  generatedDeckFilename,
  getBuildModel,
  getBuildModelOptions,
  getComparableProfilePairs,
  getDeckMixtureRows,
  getExternalDeckRating,
  getGeneratedDeckGroups,
  getSupportedThemes,
  isBuildMetadataFresh,
  safeExternalUrl,
  splitPrimerCardReferences,
} from "../lib/buildStyleUtils";
import { formatDecimal, formatNumber } from "../lib/formatters";
import CommanderName from "./CommanderName";
import "./CommanderBuildStyles.css";

function SourceLink({ url, children }) {
  const href = safeExternalUrl(url);
  return href ? <a href={href} target="_blank" rel="noreferrer">{children} ↗</a> : null;
}

function DeckCardName({ name }) {
  return <CommanderName commander={{ commander_name: name, commander_cards: [{ name }] }} linked={false} className="build-card-name" />;
}

function downloadDeckText(text, name, extension) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = generatedDeckFilename(name, extension);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

const VALIDATION_LABELS = {
  card_count: "100 cards", commander_identity: "Correct commander", color_identity: "Color identity",
  singleton: "Singleton", commander_legality: "Commander legality", sampled_pool: "Sampled card pool",
  metadata_complete: "Card metadata", metadata_fresh: "Recent card rules cache",
};

function checkedDate(value) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/Toronto" })
    : "Date unavailable";
}

function ExternalDeckRating({ rating }) {
  const result = getExternalDeckRating(rating);
  if (!result) return <p className="build-note"><strong>External rating: unverified.</strong> Use the EDHPowerLevel link to assess this exact card list.</p>;
  const stale = result.status === "stale";
  return <div className="build-external-rating" data-status={result.status}>
    <p><strong>EDHPowerLevel result{stale ? " · stale" : " · verified for this card list"}</strong><span>Checked {checkedDate(result.checked_at)}</span></p>
    <div className="build-generated-deck__metrics">
      <span><strong>{formatDecimal(result.playability_pct, 1)}%</strong> playability</span>
      {Number.isFinite(result.bracket) ? <span><strong>{result.bracket}</strong> external bracket</span> : null}
      {result.power_level !== undefined && result.power_level !== null ? <span><strong>{String(result.power_level)}</strong> external power</span> : null}
    </div>
    <p className="build-note">{stale
      ? `This older observation was ${result.inTargetRange ? "within" : "outside"} the 72.5–77.5% playability target. Recheck before treating it as current.`
      : `72.5–77.5% playability target ${result.targetMet ? "met" : "not met"}.`} External bracket and power are separate from the site’s source-informed bracket estimate.</p>
    {result.source_url ? <p className="build-source-links"><SourceLink url={result.source_url}>Open rated card list</SourceLink></p> : null}
  </div>;
}

function GeneratedPackageDeck({ deck, packageNumber }) {
  const [expanded, setExpanded] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  if (!deck || deck.status !== "generated") {
    return <div className="build-generated-unavailable"><strong>Generated deck unavailable</strong><p className="muted">{deck?.reason || "This report predates generated package decks. A refreshed analysis can add one when enough verified card data is available."}</p></div>;
  }
  const groups = getGeneratedDeckGroups(deck);
  const stats = deck.statistics || {};
  const validation = deck.validation || {};
  const bracket = validation.bracket || {};
  const externalRating = getExternalDeckRating(validation.external_rating);
  const metadata = validation.metadata || {};
  const metadataFresh = isBuildMetadataFresh(metadata);
  const highestLift = stats.highest_average_lift;
  const exportText = typeof deck.export_text === "string" ? deck.export_text : "";
  const primer = typeof deck.primer === "string" ? deck.primer : "";
  async function copyText(text, label) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setCopyStatus(`${label} copied.`);
    } catch {
      setCopyStatus(`Clipboard unavailable. Download the ${label.toLowerCase()} or select its text below.`);
    }
  }
  return <details className="build-generated-deck" onToggle={(event) => setExpanded(event.currentTarget.open)}>
    <summary><span>Generated {formatNumber(deck.card_count)}-card deck</span><span className="build-generated-deck__summary-note">Cards, tagged export & primer</span></summary>
    {expanded ? <div className="build-generated-deck__content">
      <h5>{deck.name || `Package ${packageNumber} deck`}</h5>
      <p className="build-note">A suggested deck assembled from the sampled card pool for this package. Card names support hover and keyboard previews.</p>
      <div className="build-generated-deck__metrics">
        <span><strong>{formatNumber(deck.card_count)}</strong> cards including commanders</span>
        {Number.isFinite(stats.land_count) ? <span><strong>{formatNumber(stats.land_count)}</strong> lands</span> : null}
        {Number.isFinite(stats.swaps) ? <span><strong>{formatNumber(stats.swaps)}</strong> consensus swaps</span> : null}
        {Array.isArray(stats.land_adjustments) && stats.land_adjustments.length ? <span><strong>{formatNumber(stats.land_adjustments.length)}</strong> land adjustments</span> : null}
        {externalRating?.status === "verified" && Number.isFinite(externalRating.bracket)
          ? <span><strong>{externalRating.bracket}</strong> external bracket</span>
          : Number.isFinite(bracket.recommended) ? <span><strong>{bracket.recommended}</strong> estimated bracket</span> : null}
      </div>
      {!metadataFresh ? <p className="build-generated-deck__review-note">Current card-rule freshness has not been verified. Refresh card metadata before relying on current legality.</p> : null}
      <div className="build-generated-deck__actions">
        {exportText ? <><button type="button" onClick={() => copyText(exportText, "Tagged deck")}>Copy for Moxfield</button><button type="button" onClick={() => downloadDeckText(exportText, deck.name, "txt")}>Download deck .txt</button></> : null}
        {primer ? <button type="button" onClick={() => copyText(primer, "Primer")}>Copy primer</button> : null}
      </div>
      {validation.external_url ? <p className="build-source-links"><SourceLink url={validation.external_url}>Check on EDHPowerLevel</SourceLink></p> : null}
      <ExternalDeckRating rating={validation.external_rating} />
      <p className="build-generated-deck__copy-status" role="status" aria-live="polite">{copyStatus}</p>
      <p className="build-note">Paste the tagged card list into Moxfield’s text import. Commander markers and tags are included. Cards can carry several functional and package theme tags.</p>
      <div className="build-generated-deck__card-list" tabIndex="0" role="region" aria-label={`Package ${packageNumber} generated deck cards`}>
        {groups.map((group) => <section className="build-generated-deck__role" key={group.name}>
          <h6>{group.name} <span>{formatNumber(group.count)}</span></h6>
          <table><thead><tr><th scope="col">Card</th><th scope="col">Tags</th><th scope="col">Avg. inclusion</th><th scope="col">Avg. lift</th></tr></thead>
            <tbody>{group.cards.map((card) => <tr key={card.name}>
              <th scope="row"><span className="build-generated-deck__quantity">{card.quantity}</span> <DeckCardName name={card.name} /></th>
              <td><div className="build-generated-deck__card-tags">{card.tags.map((tag) => <span key={tag}>{tag}</span>)}</div></td>
              <td>{formatBuildShare(card.average_inclusion)}</td><td>{formatBuildLift(card.average_lift)}</td>
            </tr>)}</tbody>
          </table>
        </section>)}
      </div>
      {stats.metric_note ? <p className="build-note">{stats.metric_note}</p> : null}
      <details className="build-evidence build-generated-deck__validation"><summary>Deck checks and selection</summary>
        <p className="muted">{validation.valid === true ? "The generated deck passed the structure checks recorded at generation." : "Review the recorded checks before using this deck."}</p>
        {validation.checks ? <ul className="build-generated-deck__checks">{Object.entries(validation.checks).map(([key, recorded]) => {
          const passed = recorded === true && (key !== "metadata_fresh" || metadataFresh);
          return <li key={key} data-passed={passed}><span>{passed ? "✓" : "—"}</span> {VALIDATION_LABELS[key] || key.replaceAll("_", " ")}</li>;
        })}</ul> : null}
        {metadata.checked_at || metadata.oldest_checked_at ? <p className="build-note">Rules-cache check recorded {checkedDate(metadata.checked_at)}. Oldest card record: {checkedDate(metadata.oldest_checked_at)}.{metadata.stale_cached ? ` ${formatNumber(metadata.stale_cached)} cached ${metadata.stale_cached === 1 ? "entry needed" : "entries needed"} a freshness check at generation.` : ""}</p> : null}
        {(validation.errors || []).length ? <ul>{validation.errors.map((error, index) => <li key={index}>{error}</li>)}</ul> : null}
        {Number.isFinite(bracket.recommended) ? <>
          <p className="build-note"><strong>Estimated bracket {bracket.recommended}{Number.isFinite(bracket.target) ? ` · source target ${bracket.target}` : ""}.</strong> Based on {bracket.source === "membership_weighted_median" ? "brackets reported for the package’s sampled decks" : "the available sample"} and recorded card constraints.</p>
          {Number.isFinite(bracket.game_changer_count) ? <p className="build-note">{formatNumber(bracket.game_changer_count)} Game Changers{Number.isFinite(bracket.game_changer_limit) ? ` · bracket limit ${bracket.game_changer_limit}` : ""}.</p> : null}
          {(bracket.notes || []).map((note, index) => <p className="build-note" key={index}>{note}</p>)}
        </> : bracket.reason ? <p className="build-note">{bracket.reason}</p> : null}
        {stats.selection_reason ? <p className="build-note"><strong>Candidate selection:</strong> {stats.selection_reason}</p> : null}
        {stats.method ? <p className="build-note">{stats.method}</p> : null}
        {Number.isFinite(highestLift?.lift) ? <p className="build-note">Highest card average lift: <DeckCardName name={highestLift.name} /> · {formatBuildLift(highestLift.lift)}.</p>
          : Number.isFinite(highestLift) ? <p className="build-note">Highest card average lift: {formatBuildLift(highestLift)}.</p> : null}
        {stats.profile_names?.length ? <p className="build-note">Metric profiles: {stats.profile_names.join(", ")}.</p> : null}
        {stats.anchor_source_url ? <p className="build-source-links"><SourceLink url={stats.anchor_source_url}>Sampled starting deck</SourceLink></p> : null}
      </details>
      {primer ? <details className="build-evidence build-generated-deck__primer" open><summary>Package primer</summary><div className="build-generated-deck__primer-text">{splitPrimerCardReferences(primer).map((part, index) => part.type === "card"
        ? <span key={index}>[[<DeckCardName name={part.text} />]]</span> : <span key={index}>{part.text}</span>)}</div><button type="button" onClick={() => downloadDeckText(primer, `${deck.name || "generated-deck"}-primer`, "md")}>Download primer .md</button></details> : null}
      {exportText ? <details className="build-evidence"><summary>Tagged import text</summary><textarea className="build-generated-deck__export" aria-label={`Package ${packageNumber} tagged import text`} readOnly value={exportText} spellCheck={false} /></details> : null}
    </div> : null}
  </details>;
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
      <GeneratedPackageDeck key={pkg.generated_deck?.name || pkg.id} deck={pkg.generated_deck} packageNumber={index + 1} />
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
