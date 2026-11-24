import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import BracketBadge from "../components/BracketBadge";
import CommanderImageGallery from "../components/CommanderImageGallery";
import { loadCommanderIndex } from "../lib/api";
import {
  generatedDeckFilename,
  getExternalDeckRating,
  getGeneratedDeckExport,
  safeExternalUrl,
} from "../lib/buildStyleUtils";
import { formatDecimal, formatNumber } from "../lib/formatters";
import "./SynergyDeckPage.css";

const artifacts = import.meta.glob([
  "../generated/synergy-deck.json",
  "../generated/synergy-deck-budgets.json",
], { eager: true, import: "default" });
const original = artifacts["../generated/synergy-deck.json"];
const budgets = artifacts["../generated/synergy-deck-budgets.json"];
const budgetOptions = [
  { id: "100", label: "$", cap_usd: 100 },
  { id: "500", label: "$$", cap_usd: 500 },
  { id: "1000", label: "$$$", cap_usd: 1000 },
  { id: "uncapped", label: "$$$$", cap_usd: null },
];
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const finishLabels = { nonfoil: "Nonfoil", foil: "Foil", etched: "Etched foil" };
const prototypeCommander = "the-tenth-doctor-rose-tyler";
const prototypeTitle = "The Tenth Doctor + Rose Tyler";
const catalogPageSize = 24;
const commanderColorChoices = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" };
const choosesCommanderColor = new Set(["clara oswald", "faceless one", "the prismatic piper"]);
const cardTypes = [
  ["Creature", "Creatures"], ["Artifact", "Artifacts"], ["Enchantment", "Enchantments"],
  ["Instant", "Instants"], ["Sorcery", "Sorceries"], ["Planeswalker", "Planeswalkers"], ["Battle", "Battles"],
];
const groupOrder = ["Commanders", ...cardTypes.map(([, group]) => group), "Other spells", "Lands"];
const evaluationModels = [
  ["With deck context", "neural_context"],
  ["Commanders only", "neural_commander_only"],
  ["Text similarity", "text_similarity"],
];

function reportDate(value) {
  const date = new Date(value || "");
  return Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/Toronto" })
    : null;
}

function cardGroup(card) {
  const typeLine = card.type_line?.split(" // ")[0]?.split(" — ")[0]?.split(" ") || [];
  if (typeLine.includes("Land")) return "Lands";
  return cardTypes.find(([type]) => typeLine.includes(type))?.[1] || "Other spells";
}

function quantity(cards) {
  return cards.reduce((count, card) => count + card.quantity, 0);
}

function hasPrice(value) {
  return Number.isFinite(value) && value >= 0;
}

function printingLabel(option) {
  const code = option.set_code?.toUpperCase();
  const edition = option.printing && code ? `${option.printing} (${code})` : option.printing || code;
  const collectorNumber = option.collector_number != null && option.collector_number !== ""
    ? `#${option.collector_number}` : null;
  const version = [edition, collectorNumber].filter(Boolean).join(" ");
  return [version, finishLabels[option.finish] || option.finish, option.condition].filter(Boolean).join(" · ");
}

function getShoppingList(cards, commanderTitle, tierName, totalUsd) {
  if (!hasPrice(totalUsd)) return null;
  const lines = [];
  for (const card of cards) {
    const options = card.purchase_options || [];
    if (quantity(options) !== card.quantity) return null;
    for (const option of options) {
      const url = safeExternalUrl(option.source_url);
      if (!url || !hasPrice(option.price_usd)) return null;
      lines.push(`${option.quantity} × ${card.name} — ${printingLabel(option)} — ${usd.format(option.price_usd)} each\n${url}`);
    }
  }
  return [
    `${commanderTitle} · ${tierName}\nCard Kingdom shopping list\nEstimated singles total ${usd.format(totalUsd)}`,
    ...lines,
  ].join("\n\n") + "\n";
}

function commanderName(row) {
  return row.commander_cards?.length > 1
    ? row.commander_cards.map((card) => card.name).join(" + ")
    : row.commander_name?.replace(/\s*\(Commander\)$/i, "").trim();
}

function commanderSelection(searchParams, slug) {
  const next = new URLSearchParams(searchParams);
  if (next.get("commander") !== slug) next.delete("color");
  next.set("commander", slug);
  return next;
}

function searchKey(value) {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
}

function CommanderCatalog({ entries, index, catalog, setCatalog, searchParams, heading }) {
  const search = searchKey(catalog.query.trim());
  const terms = search.split(/\s+/);
  const matches = search ? entries.filter((entry) => terms.every((term) => entry.search.includes(term))) : entries;
  const visible = matches.slice(0, catalog.limit);

  return <div className="page synergy-deck-page synergy-commander-catalog">
    <header className="panel synergy-commander-catalog__header">
      <h1 ref={heading} tabIndex={-1}>Synergy Deck</h1>
      <p className="muted">Choose a commander or pair to build a deck around their card mechanics.</p>
      <label>
        Find a commander
        <input type="search" value={catalog.query}
          onChange={(event) => setCatalog({ query: event.target.value, limit: catalogPageSize })}
          placeholder="Commander or partner name" disabled={!!index.error} />
      </label>
    </header>
    {index.loading ? <p className="muted" role="status">Loading commanders…</p> : null}
    {index.error ? <p className="error-message" role="status">Could not load the commander catalog.</p> : null}
    {!index.loading && !index.error ? <>
      <p className="muted" role="status">{matches.length
        ? `Showing ${formatNumber(visible.length)} of ${formatNumber(matches.length)} commander configurations.`
        : "No commanders match your search."}</p>
      {visible.length ? <ul className="synergy-commander-grid" aria-label="Commander catalog">
        {visible.map(({ slug, catalogName, row }) => <li key={slug}>
          <Link to={{ search: `?${commanderSelection(searchParams, slug)}` }} aria-label={catalogName}
            className="synergy-commander-grid__tile">
            <div className="synergy-commander-grid__art"><CommanderImageGallery commander={row} variant="tile" /></div>
            <span>{catalogName}</span>
          </Link>
        </li>)}
      </ul> : null}
      {visible.length < matches.length ? <button className="synergy-commander-catalog__more" type="button"
        onClick={() => setCatalog((current) => ({ ...current, limit: current.limit + catalogPageSize }))}>
        Load more commanders
      </button> : null}
    </> : null}
  </div>;
}

function useGeneratedBundle(entry, chosenColor) {
  const runtime = useRef(null);
  const requestId = useRef(0);
  const [result, setResult] = useState(null);
  const selection = entry && (!entry.choosesColor || chosenColor) ? `${entry.slug}|${chosenColor || ""}` : null;

  useEffect(() => () => {
    runtime.current?.terminate();
    runtime.current = null;
  }, []);

  useEffect(() => {
    if (!selection) return;
    const id = ++requestId.current;
    const worker = runtime.current ||= new Worker(new URL("../lib/synergyDeck.worker.js", import.meta.url), { type: "module" });
    const receive = ({ data }) => {
      if (data?.id !== id) return;
      if (data.bundle?.tiers?.length) setResult({ selection, bundle: data.bundle });
      else setResult({ selection, error: data.error || "The generated deck report was empty." });
    };
    function fail() {
      setResult({ selection, error: "The deck builder could not load. Try refreshing the page." });
      worker.terminate();
      runtime.current = null;
    }

    worker.addEventListener("message", receive);
    worker.addEventListener("error", fail);
    worker.postMessage({
      id,
      commanders: entry.names,
      colorIdentity: chosenColor ? [...new Set([...entry.colors, chosenColor])] : entry.colors,
      ...(chosenColor ? { chosenColor } : {}),
      baseUrl: new URL(`${import.meta.env.BASE_URL}synergy-assets/`, document.baseURI).href,
    });
    return () => {
      worker.removeEventListener("message", receive);
      worker.removeEventListener("error", fail);
    };
  }, [selection, entry, chosenColor]);

  if (!selection) return { loading: false };
  return result?.selection === selection ? result : { loading: true };
}

function BudgetPicker({ selected, searchParams }) {
  return <nav className="synergy-deck-budgets" aria-label="Deck budget">
    {budgetOptions.map(({ id, label, cap_usd }) => {
      const next = new URLSearchParams(searchParams);
      next.set("budget", id);
      return <Link key={id} to={{ search: `?${next}` }} aria-current={selected === id ? "true" : undefined}>
        <strong>{label}</strong>
        <span>{cap_usd == null ? "Uncapped" : `Up to $${formatNumber(cap_usd)} USD`}</span>
      </Link>;
    })}
  </nav>;
}

function PriceProvenance({ tier, pricing }) {
  const sourceUrl = safeExternalUrl(pricing?.source_url);
  const source = pricing?.source;
  const date = reportDate(pricing?.checked_at);
  const missing = tier.unpriced_cards?.length || 0;
  const note = tier.price_notes || pricing?.description || pricing?.methodology;

  return <div className="synergy-deck-price">
    {!hasPrice(tier.total_usd) && missing > 0 ? <details>
      <summary>Full estimate unavailable. {formatNumber(missing)} {missing === 1 ? "card is" : "cards are"} unpriced.</summary>
      <p>{tier.unpriced_cards.join(", ")}</p>
    </details> : null}
    {!hasPrice(tier.total_usd) && missing === 0 ? <p>Price estimate unavailable.</p> : null}
    {source ? <p>USD prices from {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer">{source}</a> : source}{date ? ` on ${date}` : ""}.</p> : null}
    {note ? <p>{note}</p> : null}
  </div>;
}

function ExternalBracket({ rating, deckHash, externalUrl }) {
  const recorded = deckHash ? getExternalDeckRating(rating, undefined, deckHash) : null;
  const stale = recorded?.status === "stale";
  const link = safeExternalUrl(recorded?.source_url || externalUrl) || "https://edhpowerlevel.com/";
  const checked = reportDate(recorded?.checked_at);

  return <section className="panel synergy-deck-rating" aria-labelledby="synergy-rating-heading">
    <h2 id="synergy-rating-heading">EDHPowerLevel estimate</h2>
    {recorded?.reportedBracket ? <>
      <BracketBadge bracketKey={recorded.reportedBracket} />
      <p className="muted">{checked ? `Exact list checked ${checked}.` : "Exact list checked."}{stale ? " This estimate is historical." : ""}</p>
      <dl className="synergy-deck-rating__details">
        {recorded.power_level != null ? <div><dt>Power score</dt><dd>{recorded.power_level}</dd></div> : null}
        {recorded.playabilityKnown ? <div><dt>Playability</dt><dd>{formatDecimal(recorded.playability_pct, 1)}%</dd></div> : null}
      </dl>
      {recorded.playabilityKnown ? <p className="muted">{recorded.inTargetRange ? "Within" : "Outside"} the 72.5%–77.5% playability target.</p> : null}
      {recorded.bracketMismatch ? <p className="muted">The power score corresponds to Bracket {recorded.scoreBracket}. The badge shows the site’s reported bracket.</p> : null}
    </> : <p className="muted">{["failed", "blocked"].includes(rating?.status)
      ? "The exact-list check could not be completed."
      : "This exact list is ready to check on EDHPowerLevel."}</p>}
    <a className="synergy-deck-link" href={link} target="_blank" rel="noreferrer">{recorded ? "Open on EDHPowerLevel" : "Check on EDHPowerLevel"} ↗</a>
  </section>;
}

function CardPrintings({ card }) {
  const options = card.purchase_options || [];
  if (!options.length) return null;
  return <span className="synergy-deck-list__printings">{options.map((option) => {
    const url = safeExternalUrl(option.source_url);
    const label = printingLabel(option);
    const key = [option.scryfall_id, option.finish, option.condition, option.source_url].join("|");
    return <span className="synergy-deck-list__printing" key={key}>
      {card.quantity > 1 ? `${option.quantity} × ` : null}
      {url ? <a href={url} target="_blank" rel="noreferrer" aria-label={`Card Kingdom, ${card.name}, ${label}`}>{label}</a> : label}
      {options.length > 1 && hasPrice(option.price_usd) ? ` · ${usd.format(option.price_usd)} ea.` : null}
    </span>;
  })}</span>;
}

function DeckList({ commanders, mainDeck }) {
  const [query, setQuery] = useState("");
  const search = query.trim().toLowerCase();
  const matches = (card) => card.name.toLowerCase().includes(search);
  const grouped = new Map([["Commanders", commanders.filter(matches)]]);
  for (const card of mainDeck.filter(matches)) {
    const group = cardGroup(card);
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push(card);
  }

  return <section className="panel synergy-deck-list" aria-labelledby="synergy-list-heading">
    <header>
      <h2 id="synergy-list-heading">Deck list</h2>
      <label>Find a card <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Card name" /></label>
    </header>
    {[...grouped.values()].every((cards) => !cards.length) ? <p className="muted" role="status">No cards match that name.</p> : null}
    <div className="synergy-deck-list__groups">
      {groupOrder.map((group) => {
        const cards = grouped.get(group);
        if (!cards?.length) return null;
        return <section key={group} className="synergy-deck-list__group">
          <h3>{group} <span>{quantity(cards)}</span></h3>
          <ul>{cards.toSorted((a, b) => a.name.localeCompare(b.name)).map((card) => {
            const link = safeExternalUrl(card.scryfall_uri);
            const reason = typeof card.synergy_signal === "string" ? card.synergy_signal : card.reason;
            const unitPrice = hasPrice(card.price_usd);
            const price = unitPrice ? card.price_usd : card.price_total_usd;
            return <li key={card.name}>
              <span className="synergy-deck-list__quantity">{card.quantity}</span>
              <span>{link ? <a href={link} target="_blank" rel="noreferrer">{card.name}</a> : card.name}
                <CardPrintings card={card} />
                {reason ? <small>{reason}</small> : null}</span>
              {card.mana_cost || hasPrice(price) ? <span className="synergy-deck-list__cost">
                {card.mana_cost || null}
                {hasPrice(price) ? <span className="synergy-deck-list__price" title={unitPrice ? "USD per copy" : "USD for all copies"}>
                  {usd.format(price)}{card.quantity > 1 ? (unitPrice ? " ea." : " total") : ""}
                </span> : null}
              </span> : null}
            </li>;
          })}</ul>
        </section>;
      })}
    </div>
  </section>;
}

function ModelSummary({ synergy, deck, generated }) {
  const construction = deck.construction || {};
  const metrics = [
    ["Training decks", synergy.training_decks],
    ["Catalog cards", synergy.catalog_cards],
    ["Legal spell candidates", construction.candidate_spell_count],
    ["Candidate lists evaluated", synergy.evaluated_decks ?? construction.search_starts],
  ].filter(([, value]) => Number.isFinite(value));
  const score = synergy.objective_score ?? deck.synergy_score;
  const validation = deck.validation || {};
  const evaluation = synergy.evaluation || {};
  const hitRates = evaluationModels.map(([label, key]) => [label, evaluation[key]?.recall_at_10])
    .filter(([, value]) => Number.isFinite(value));

  return <details className="panel synergy-deck-method">
    <summary>Generation details</summary>
    {synergy.model ? <p>{synergy.model}</p> : null}
    {generated ? <p className="muted">Generated {generated}.</p> : null}
    {metrics.length ? <dl>{metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatNumber(value)}</dd></div>)}</dl> : null}
    {Number.isFinite(score) ? <p className="muted">Model selection score {formatDecimal(score, 3)}. {construction.score_explanation}</p> : null}
    {validation.valid ? <p className="muted">The list passed the card count, commander configuration, color identity, singleton, and format legality checks.</p> : null}
    {hitRates.length ? <>
      <h3>Held-out card ranked in the top 10</h3>
      <p className="muted">{formatNumber(evaluation.queries)} test queries with up to {formatNumber(evaluation.max_candidates_per_query)} legal candidates per query.</p>
      <dl>{hitRates.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{formatDecimal(value * 100, 1)}%</dd></div>)}</dl>
    </> : null}
  </details>;
}

function DeckExport({ deck, allCards, tier, commanderTitle }) {
  const [copyStatus, setCopyStatus] = useState("");
  const exportText = deck.export_text || getGeneratedDeckExport({ commanders: deck.commanders, cards: allCards });
  const downloadUrl = `data:text/plain;charset=utf-8,${encodeURIComponent(exportText)}`;
  const tierName = tier?.cap_usd == null ? "Uncapped" : `${tier.cap_usd} USD`;
  const filename = generatedDeckFilename(tier ? `${commanderTitle} ${tierName}` : commanderTitle);
  const shoppingList = getShoppingList(allCards, commanderTitle, tierName, tier?.total_usd);
  const shoppingUrl = shoppingList ? `data:text/plain;charset=utf-8,${encodeURIComponent(shoppingList)}` : null;

  async function copyDeck() {
    try {
      await navigator.clipboard.writeText(exportText);
      setCopyStatus("Deck list copied.");
    } catch {
      setCopyStatus("Clipboard access failed. Download the deck list to import it.");
    }
  }

  return <>
    <div className="synergy-deck-hero__actions">
      <button type="button" onClick={copyDeck}>Copy for Moxfield</button>
      <a href={downloadUrl} download={filename}>Download .txt</a>
      {shoppingUrl ? <a href={shoppingUrl} download={generatedDeckFilename(`${commanderTitle} ${tierName} shopping list`)}>Download shopping list</a> : null}
    </div>
    {copyStatus ? <p role="status" className="synergy-deck-hero__copy">{copyStatus}</p> : null}
  </>;
}

function DeckHero({ deck, commanderTitle, summary, allCards, tier, priceDetails, budgetPicker, colorPicker, unavailableReason, heading }) {
  const landCount = quantity(allCards.filter((card) => cardGroup(card) === "Lands"));
  const art = deck?.commanders.filter((card) => card.image_url) || [];

  return <header className="panel synergy-deck-hero">
    <div>
      <p className="eyebrow">Oracle mechanics</p>
      <h1 ref={heading} tabIndex={-1}>{commanderTitle}</h1>
      {summary ? <p className="synergy-deck-hero__summary">{summary}</p> : null}
      {colorPicker}
      {budgetPicker}
      {deck ? <>
        <ul className="synergy-deck-hero__stats">
          {hasPrice(tier?.total_usd) ? <li aria-live="polite"><strong>{usd.format(tier.total_usd)}</strong> estimated total</li> : null}
          <li><strong>{quantity(allCards)}</strong> cards</li>
          <li><strong>{landCount}</strong> lands</li>
          {Number.isFinite(deck.mana_diagnostics?.average_nonland_mana_value)
            ? <li><strong>{formatDecimal(deck.mana_diagnostics.average_nonland_mana_value, 1)}</strong> average spell mana value</li> : null}
        </ul>
        {priceDetails}
        <DeckExport key={`${commanderTitle}|${tier?.id}`} deck={deck} allCards={allCards} tier={tier} commanderTitle={commanderTitle} />
      </> : <p role="status" className="muted">{unavailableReason || "The generated deck is unavailable."}</p>}
    </div>
    {art.length ? <CommanderImageGallery commander={{ commander_cards: art }} variant="detail" /> : null}
  </header>;
}

export default function SynergyDeckPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [index, setIndex] = useState({ entries: [], loading: true, error: null });
  const [catalog, setCatalog] = useState({ query: "", limit: catalogPageSize });
  const commander = searchParams.get("commander");
  const heading = useRef(null);
  const previousCommander = useRef(commander);

  useEffect(() => {
    let active = true;
    loadCommanderIndex().then((entries) => {
      if (active) setIndex({ entries: Array.isArray(entries) ? entries : [], loading: false, error: null });
    }).catch((error) => {
      if (active) setIndex({ entries: [], loading: false, error });
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    window.scrollTo(0, 0);
    if (previousCommander.current !== commander) {
      heading.current?.focus({ preventScroll: true });
      previousCommander.current = commander;
    }
  }, [commander]);

  const commanderEntries = useMemo(() => index.entries.flatMap((row) => {
    const slug = row.commander_slug;
    const name = commanderName(row);
    const indexName = row.commander_name?.replace(/\s*\(Commander\)$/i, "") || "";
    const cardNames = row.commander_cards?.map((card) => card.name).filter(Boolean);
    const names = cardNames?.length ? cardNames : row.scryfall_card_names?.length ? row.scryfall_card_names : [name];
    const catalogName = (cardNames?.length > 1 ? indexName.replace(" // ", " + ") : indexName) || name;
    return slug && name ? [{
      slug, name, names, row, catalogName, search: searchKey(`${name} ${indexName} ${catalogName} ${slug}`), decks: Number(row.total_decks) || 0,
      colors: Array.isArray(row.color_identity) ? row.color_identity : [],
      choosesColor: names.some((card) => choosesCommanderColor.has(searchKey(card))),
    }] : [];
  }).sort((a, b) => b.decks - a.decks || a.name.localeCompare(b.name)), [index.entries]);

  if (!commander) return <CommanderCatalog entries={commanderEntries} index={index} catalog={catalog}
    setCatalog={setCatalog} searchParams={searchParams} heading={heading} />;

  const selectedCommander = commanderEntries.find((entry) => entry.slug === commander);
  return <CommanderDeck commander={commander} selectedCommander={selectedCommander} index={index}
    searchParams={searchParams} setSearchParams={setSearchParams} heading={heading} />;
}

function CommanderDeck({ commander, selectedCommander, index, searchParams, setSearchParams, heading }) {
  const requested = searchParams.get("budget");
  const selected = budgetOptions.find(({ id }) => id === requested) || budgetOptions.at(-1);
  const isPrototype = commander === prototypeCommander;
  const colorParam = searchParams.get("color");
  const chosenColor = selectedCommander?.choosesColor && Object.hasOwn(commanderColorChoices, colorParam) ? colorParam : null;
  const generatedBundle = useGeneratedBundle(isPrototype ? null : selectedCommander, chosenColor);
  const activeBudgets = isPrototype ? budgets : generatedBundle.bundle;
  const hasBudgets = activeBudgets?.tiers?.length > 0;
  const tier = hasBudgets ? { ...activeBudgets.tiers.find(({ id }) => id === selected.id), ...selected } : null;
  const report = tier?.report ?? (isPrototype && (!tier || selected.id === "uncapped") ? original : null);
  const budgetPicker = hasBudgets || selectedCommander && !isPrototype
    ? <BudgetPicker selected={selected.id} searchParams={searchParams} /> : null;
  const deck = report?.deck?.commanders?.length && report.deck.cards?.length ? report.deck : null;

  const synergy = report?.synergy || {};
  const commanderCards = deck?.commanders || [];
  const list = deck?.cards || [];
  const commanderNames = new Set(commanderCards.map((card) => card.name));
  const commanderRows = new Map(list.filter((card) => commanderNames.has(card.name)).map((card) => [card.name, card]));
  const commanders = commanderCards.map((card) => ({ ...card, ...commanderRows.get(card.name) }));
  const mainDeck = list.filter((card) => !commanderNames.has(card.name));
  const commanderTitle = deck ? commanderCards.map((card) => card.name).join(" + ")
    : selectedCommander?.name || (isPrototype ? prototypeTitle : "Synergy Deck");
  const generated = reportDate(report?.generated_at);
  let unavailableReason = tier?.unavailable_reason;
  if (!isPrototype) {
    if (index.loading) unavailableReason = "Loading commander details…";
    else if (index.error) unavailableReason = "The deck builder needs the commander catalog. Try refreshing the page.";
    else if (!selectedCommander) unavailableReason = "Commander not found. Choose a commander from the catalog.";
    else if (selectedCommander.choosesColor && !chosenColor) unavailableReason = "Choose a commander color to build a deck.";
    else if (generatedBundle.loading) unavailableReason = "Building legal deck lists for each budget…";
    else if (generatedBundle.error) unavailableReason = generatedBundle.error;
  }
  const colorPicker = selectedCommander?.choosesColor ? <label className="synergy-deck-color">
    Commander color
    <select value={chosenColor || ""} onChange={(event) => {
      const next = new URLSearchParams(searchParams);
      if (event.target.value) next.set("color", event.target.value);
      else next.delete("color");
      setSearchParams(next);
    }}>
      <option value="">Choose a color</option>
      {Object.entries(commanderColorChoices).map(([color, name]) => <option key={color} value={color}>{name}</option>)}
    </select>
  </label> : null;

  const catalogParams = new URLSearchParams(searchParams);
  catalogParams.delete("commander");
  catalogParams.delete("color");

  return <article className="page synergy-deck-page">
    <div><Link className="detail-back-link" to={{ search: catalogParams.toString() }}>← Back to commander grid</Link></div>
    <DeckHero deck={deck} commanderTitle={commanderTitle} summary={synergy.summary} allCards={[...commanders, ...mainDeck]} tier={tier}
      priceDetails={tier ? <PriceProvenance tier={tier} pricing={activeBudgets.pricing} /> : null} budgetPicker={budgetPicker}
      colorPicker={colorPicker} unavailableReason={unavailableReason} heading={heading} />
    {deck ? <>
      <div className="synergy-deck-overview">
        {synergy.highlights?.length ? <section className="panel synergy-deck-plan" aria-labelledby="synergy-plan-heading">
          <h2 id="synergy-plan-heading">How the deck plays</h2>
          <dl>{synergy.highlights.map(({ title, description }) => <div key={title}><dt>{title}</dt><dd>{description}</dd></div>)}</dl>
        </section> : null}
        <div className="synergy-deck-sidebar">
          <ExternalBracket rating={report.external_rating ?? deck.validation?.external_rating}
            deckHash={deck.deck_sha256 ?? deck.cardlist_sha256}
            externalUrl={report.external_url ?? deck.validation?.external_url} />
          <ModelSummary synergy={synergy} deck={deck} generated={generated} />
        </div>
      </div>
      <DeckList key={`${commander}|${chosenColor ?? ""}`} commanders={commanders} mainDeck={mainDeck} />
    </> : null}
  </article>;
}
