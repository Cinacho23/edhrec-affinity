import { commanderOracleProfile, oracleProfile, oracleSynergy } from "./oracleSynergy.js";
import { neuralScores } from "./synergyDeckRanker.js";

const colorLetters = "WUBRG";
const basicTypes = ["plains", "island", "swamp", "mountain", "forest"];
const tierSpecs = [["100", "$", 10000], ["500", "$$", 50000], ["1000", "$$$", 100000], ["uncapped", "$$$$", null]];
const cardKey = (name) => name.normalize("NFKC").replaceAll("’", "'").toLowerCase().replaceAll("ß", "ss").trim();
const bits = (value) => [...Array(5).keys()].filter((index) => value & (1 << index));
const spellPrice = (card) => card.offers?.[0]?.price_cents ?? Infinity;
const lesserPrice = (left, right) => spellPrice(left) - spellPrice(right) || left.name.localeCompare(right.name);
const oracleText = (card) => card.card_faces?.length ? card.card_faces.map((face) => face.oracle_text).join("\n") : card.oracle_text || "";
const indexedNames = new WeakMap();

export function maskForColors(colors = []) {
  return [...colors].reduce((mask, color) => colorLetters.includes(color) && color.length === 1
    ? mask | 1 << colorLetters.indexOf(color) : mask, 0);
}

export function commanderCards(names, catalog) {
  if (!indexedNames.has(catalog)) {
    const entries = new Map();
    for (const card of catalog) {
      for (const name of [card.name, ...(card.aliases || [])]) {
        const key = cardKey(name);
        if (!entries.has(key)) entries.set(key, card);
        else if (entries.get(key) !== card) entries.set(key, null);
      }
    }
    indexedNames.set(catalog, entries);
  }
  const entries = indexedNames.get(catalog);
  return names.map((name) => entries.get(cardKey(name)));
}

function canShareCommanders(left, right) {
  const text = (card) => cardKey(oracleText(card));
  const partners = (card) => text(card).split("\n").map((line) => line.split("(")[0].trim())
    .filter((line) => /^(?:partner(?:\s*[—–-].+)?|friends forever)$/.test(line));
  if (!/legendary/i.test(left.type_line) || !/legendary/i.test(right.type_line)) return false;
  if (partners(left).some((role) => partners(right).includes(role))) return true;
  if (text(left).includes(`partner with ${cardKey(right.name)}`) && text(right).includes(`partner with ${cardKey(left.name)}`)) return true;
  return [[left, right], [right, left]].some(([card, other]) => {
    const types = other.type_line.split(" // ")[0].toLowerCase();
    const canChooseBackground = text(card).includes("choose a background") && /enchantment.*background/.test(types);
    const joinsDoctor = text(card).includes("doctor's companion") && /creature\s*—\s*time lord doctor$/.test(types.split("legendary ").at(-1));
    return canChooseBackground || joinsDoctor;
  });
}

export function commanderColors(commanders, chosenColor) {
  if (commanders.some((card) => !card)) throw new Error("This commander is missing from the legal Oracle snapshot.");
  const legal = commanders.length === 1 ? commanders[0].can_command
    : commanders.length === 2 && commanders[0] !== commanders[1] && canShareCommanders(...commanders);
  if (!legal) {
    throw new Error("This command zone is unavailable under the Commander rules.");
  }
  const chosen = maskForColors(chosenColor || "");
  const flexible = commanders.some((card) => card.chooses_color);
  if (flexible && bits(chosen).length !== 1) throw new Error("Choose a pregame color for this commander.");
  return commanders.reduce((mask, card) => mask | card.color_mask, flexible ? chosen : 0);
}

function quote(card, quantity) {
  if (!quantity) return { options: [], cents: 0 };
  let remaining = quantity;
  const options = [];
  let cents = 0;
  for (const offer of card.offers || []) {
    const take = Math.min(remaining, offer.stock);
    if (take <= 0) continue;
    options.push({ ...offer, quantity: take, price_usd: offer.price_cents / 100 });
    cents += take * offer.price_cents;
    remaining -= take;
    if (!remaining) return { options, cents };
  }
  return { options: [], cents: Infinity };
}

function landSources(card, colors, typedSources) {
  const text = oracleText(card).toLowerCase();
  const search = text.match(/search your library for ([^.\n]+?)\bcard\b/);
  if (search && !/token|any color/.test(search[1])) {
    if (search[1].includes("basic land")) return colors;
    const types = basicTypes.reduce((result, type, index) => search[1].includes(type) ? result | 1 << index : result, 0);
    if (types) return typedSources.reduce((result, land) => land.landTypes & types ? result | land.source : result, 0);
  }
  return card.produced_mask & colors;
}

function landTypes(card) {
  const text = card.type_line.toLowerCase().split("—")[1] || "";
  return basicTypes.reduce((result, type, index) => text.split(/\s/).includes(type) ? result | 1 << index : result, 0);
}

class BudgetUnavailable extends Error {}

function pickLands(pool, colors, total, cap) {
  const lands = new Map();
  const allLands = pool.filter((card) => card.land && (cap === Infinity || Number.isFinite(spellPrice(card))));
  const base = allLands.filter((card) => card.basic);
  const typicalTypes = base.map((card) => ({ landTypes: landTypes(card), source: card.produced_mask & colors }));
  const eligible = allLands.filter((card) => landSources(card, colors, typicalTypes) || card.produces_colorless);
  const cheapest = cap === Infinity ? [] : eligible.flatMap((card) => {
    const prices = [];
    for (let copy = 0; copy < Math.min(card.basic ? total : 1, total); copy++) {
      const cents = quote(card, copy + 1).cents - quote(card, copy).cents;
      if (!Number.isFinite(cents)) break;
      prices.push({ card, copy, cents });
    }
    return prices;
  }).sort((a, b) => a.cents - b.cents);
  let spent = 0;
  const count = (card) => lands.get(card) || 0;
  function add(card) {
    const previous = count(card);
    lands.set(card, previous + 1);
    if (cap !== Infinity) spent += quote(card, previous + 1).cents - quote(card, previous).cents;
  }
  function remainingLandCosts(slots) {
    let sum = 0;
    let next = Infinity;
    const reserved = new Set();
    for (const unit of cheapest) {
      if (unit.copy < count(unit.card)) continue;
      if (reserved.size >= slots) { next = unit.cents; break; }
      reserved.add(unit);
      sum += unit.cents;
    }
    const reservedCards = new Set([...reserved].filter((unit) => unit.copy === count(unit.card)).map(({ card }) => card));
    return { sum: reserved.size < slots ? Infinity : sum, next, reservedCards };
  }
  function fits(card, price, reservation) {
    const completion = reservation.reservedCards.has(card) ? reservation.next : price;
    return spent + completion + reservation.sum <= cap;
  }
  const anchorSeeds = bits(colors).map((index) => base.filter((card) => card.produced_mask & (1 << index)).toSorted(lesserPrice)[0]).filter(Boolean);
  let slots = total;
  const seeds = anchorSeeds.length ? anchorSeeds : base.filter((card) => card.produces_colorless).toSorted(lesserPrice).slice(0, 1);
  for (const card of new Set(seeds)) {
    const copies = Math.min(3, Math.floor(total / Math.max(1, anchorSeeds.length)));
    for (let index = 0; index < copies && slots > 0; index++) {
      const price = cap === Infinity ? 0 : quote(card, count(card) + 1).cents - quote(card, count(card)).cents;
      if (cap === Infinity || Number.isFinite(price) && fits(card, price, remainingLandCosts(slots - 1))) { add(card); slots--; }
    }
  }
  const early = new Map();
  while (slots > 0) {
    const typed = [...lands.keys()].map((card) => ({ landTypes: landTypes(card), source: card.produced_mask & colors }));
    early.clear();
    for (const [card, quantity] of lands) for (const shade of bits(landSources(card, colors, typed))) {
      early.set(shade, (early.get(shade) || 0) + quantity * (card.land_quality ?? .75));
    }
    let chosen, maximum = -Infinity;
    const reservation = cap === Infinity ? null : remainingLandCosts(slots - 1);
    for (const card of eligible) {
      const previous = count(card);
      if (previous && !card.basic) continue;
      const price = cap === Infinity ? 0 : quote(card, previous + 1).cents - quote(card, previous).cents;
      if (!Number.isFinite(price) || (reservation && !fits(card, price, reservation))) continue;
      const source = landSources(card, colors, typed);
      if (!source && !card.produces_colorless) continue;
      const quality = card.land_quality ?? .75;
      const fixing = bits(source).reduce((value, shade) => value + Math.max(.28, 1 - (early.get(shade) || 0) / 24), 0);
      const costPenalty = cap === Infinity ? 0 : .7 * Math.log1p(price / Math.max(80, cap / total));
      const score = quality * (colors ? .35 + fixing : 1.05) - costPenalty - .045 * previous;
      if (score > maximum) { chosen = card; maximum = score; }
    }
    if (!chosen) throw new BudgetUnavailable("The quoted stock could not fill a functional mana base within this budget.");
    add(chosen);
    slots--;
  }
  return { lands, spent };
}

function spellRoles(card) {
  const roles = new Set(card.roles || []);
  const result = [];
  if (card.cmc <= 3 && (roles.has("Ramp") || roles.has("Fast Mana"))) result.push("ramp");
  if (roles.has("Card Advantage")) result.push("draw");
  if (roles.has("Removal") || roles.has("Wipe")) result.push("interaction");
  return result;
}

function prepareSpells(pool, commanders, model) {
  const profile = commanderOracleProfile(commanders);
  const scored = neuralScores(pool, commanders, [], model);
  return pool.map((card) => {
    const fit = oracleSynergy(profile, oracleProfile(card));
    return { card, roles: spellRoles(card), fit, score: fit.score + .70 * (scored.get(card.name) || 0) };
  });
}

function reserveForRemaining(cheap, picked, quantity) {
  const reserved = new Set();
  let subtotal = 0;
  let alternate = 0;
  for (const card of cheap) {
    if (picked.has(card)) continue;
    if (reserved.size >= quantity) { alternate = spellPrice(card); break; }
    reserved.add(card);
    subtotal += spellPrice(card);
  }
  return { reserved, subtotal, alternate };
}

function pickSpells(prepared, commanders, count, budget, model) {
  const shortlist = prepared.filter(({ card }) => budget === Infinity || spellPrice(card) <= budget);
  if (shortlist.length < count) throw new BudgetUnavailable("Too few legally playable cards have a usable quote at this cap.");
  const cheap = shortlist.map(({ card }) => card).toSorted(lesserPrice);
  const picked = new Set(), chosen = [], roles = { ramp: 0, draw: 0, interaction: 0 };
  const effects = new Map();
  const targets = { ramp: Math.max(...commanders.map((card) => card.cmc)) >= 5 ? 10 : 8, draw: 9, interaction: 9 };
  const contextPool = shortlist.toSorted((a, b) => b.score - a.score).slice(0, 550).map(({ card }) => card);
  let contextScores = new Map(), remaining = budget;
  for (let slot = 0; slot < count; slot++) {
    if (slot && slot % 16 === 0) contextScores = neuralScores(contextPool, commanders, chosen.map(({ card }) => card), model);
    const reserve = budget === Infinity ? null : reserveForRemaining(cheap, picked, count - slot - 1);
    let best, maximum = -Infinity;
    for (const entry of shortlist) {
      const { card, fit } = entry;
      if (picked.has(card)) continue;
      const price = spellPrice(card);
      if (reserve) {
        const cost = reserve.reserved.has(card) ? reserve.subtotal + reserve.alternate : reserve.subtotal + price;
        if (cost > remaining) continue;
      }
      const roleBoost = entry.roles.reduce((sum, role) => sum + (roles[role] < targets[role] ? .76 : role === "ramp" ? -.85 : -.15), 0);
      const overlap = (fit.matches || []).reduce((sum, match) => sum + (effects.get(match.id) || 0), 0);
      const curve = .10 * Math.max(0, card.cmc - 4);
      const budgetPenalty = reserve ? .08 * Math.log1p(price / 100) : Number.isFinite(price) ? 0 : .41;
      const score = entry.score + .36 * (contextScores.get(card.name) || 0) + roleBoost - Math.min(.68, .06 * overlap) - curve - budgetPenalty;
      if (score > maximum) { best = entry; maximum = score; }
    }
    if (!best) throw new BudgetUnavailable("The priced card pool could not fill 100 legal slots at this cap.");
    picked.add(best.card);
    chosen.push(best);
    if (budget !== Infinity) remaining -= spellPrice(best.card);
    for (const role of best.roles) roles[role]++;
    for (const { id } of best.fit.matches || []) effects.set(id, (effects.get(id) || 0) + 1);
  }
  return { chosen, roles };
}

function outputRow(card, quantity, role, reason = null) {
  const price = quote(card, quantity);
  const equalPrices = price.options.length && price.options.every((offer) => offer.price_cents === price.options[0].price_cents);
  return { name: card.name, quantity, type_line: card.type_line, mana_cost: card.mana_cost, mana_value: card.cmc,
    scryfall_uri: card.scryfall_uri, image_url: card.image_url, primary_role: role, synergy_signal: reason,
    purchase_options: price.options, price_total_usd: Number.isFinite(price.cents) ? price.cents / 100 : null,
    price_usd: equalPrices ? price.options[0].price_usd : null };
}

export async function exactDeckHash(rows) {
  const pairs = rows.map((card) => [card.quantity, cardKey(card.name)]).sort((a, b) => a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(pairs)));
  return Array.from(new Uint8Array(bytes), (value) => value.toString(16).padStart(2, "0")).join("");
}

function onCurveRate(chosen, landCount, ramp, commanderCount) {
  const population = 99 - commanderCount;
  const probabilities = new Map([[0, 1]]);
  let sum = 0;
  for (const { card } of chosen) {
    const turn = Math.min(10, Math.ceil(card.cmc));
    if (!probabilities.has(turn)) {
      // Early ramp only starts helping after making the first few land drops.
      const sources = landCount + (turn >= 3 ? Math.round(ramp * .35) : 0);
      let distribution = [1];
      for (let draw = 0; draw < 6 + turn; draw++) {
        const next = Array(distribution.length + 1).fill(0);
        distribution.forEach((weight, hits) => {
          const hit = (sources - hits) / (population - draw);
          next[hits] += weight * (1 - hit);
          next[hits + 1] += weight * hit;
        });
        distribution = next;
      }
      probabilities.set(turn, distribution.slice(turn).reduce((total, value) => total + value, 0));
    }
    sum += probabilities.get(turn);
  }
  return 100 * sum / chosen.length;
}

async function assembleTier(spec, commanders, pool, prepared, colors, landCount, model, manifest, chosenColor) {
  const [id, label, cap] = spec;
  const tier = { id, label, cap_usd: cap === null ? null : cap / 100 };
  try {
    const commanderCost = commanders.reduce((sum, card) => sum + spellPrice(card), 0);
    if (cap !== null && !Number.isFinite(commanderCost)) throw new BudgetUnavailable("Card Kingdom has no verified quote for every commander.");
    if (cap !== null && commanderCost >= cap) throw new BudgetUnavailable("The commanders consume this price cap before adding cards.");
    const spellCount = 100 - commanders.length - landCount;
    const funds = cap === null ? Infinity : cap - commanderCost;
    const spellFloor = cap === null ? 0 : prepared.map(({ card }) => card).toSorted(lesserPrice).slice(0, spellCount)
      .reduce((sum, card) => sum + spellPrice(card), 0);
    if (spellFloor > funds) throw new BudgetUnavailable("Too few legally playable spells fit this price cap.");
    const landBudget = cap === null ? Infinity : Math.min(funds - spellFloor, Math.floor(funds * .34));
    let manaBase;
    try {
      manaBase = pickLands(pool, colors, landCount, landBudget);
    } catch (error) {
      if (!(error instanceof BudgetUnavailable) || cap === null || landBudget === funds - spellFloor) throw error;
      manaBase = pickLands(pool, colors, landCount, funds - spellFloor);
    }
    const { lands, spent } = manaBase;
    const { chosen, roles } = pickSpells(prepared, commanders, spellCount,
                                         cap === null ? Infinity : cap - commanderCost - spent, model);
    const commandRows = commanders.map((card) => outputRow(card, 1, "Commander"));
    const main = [...Array.from(lands, ([card, quantity]) => outputRow(card, quantity, "Lands")),
      ...chosen.map(({ card, fit }) => outputRow(card, 1, "Spell", fit.reason))];
    const cards = [...commandRows, ...main];
    const unpriced = cards.filter((row) => row.price_total_usd == null).map((row) => row.name);
    const cents = cards.reduce((sum, row) => sum + Math.round((row.price_total_usd || 0) * 100), 0);
    if (cap !== null && (unpriced.length || cents > cap)) throw new BudgetUnavailable("The full quoted card list exceeds this cap.");
    if (cards.reduce((sum, card) => sum + card.quantity, 0) !== 100 || new Set(cards.map((row) => cardKey(row.name))).size !== cards.length) {
      throw new Error("The deck failed its card count or singleton check.");
    }
    const hash = await exactDeckHash(cards);
    const text = (row) => `${row.quantity} ${row.name.replace(/\s*(\/+)\s*/g, " $1 ")}`;
    const deck = { deck_sha256: hash, card_count: 100, commanders: commandRows, cards,
      export_text: [...commandRows.map((row) => `${row.quantity} ${row.name} *CMDR*`), ...main.map((row) => `${row.quantity} ${row.name}`)].join("\n") + "\n",
      mana_diagnostics: { land_count: landCount, average_nonland_mana_value: chosen.reduce((sum, { card }) => sum + card.cmc, 0) / chosen.length,
        local_on_curve_estimate: onCurveRate(chosen, landCount, roles.ramp, commanders.length) },
      construction: { candidate_spell_count: prepared.length, spell_role_counts: roles }, validation: { valid: true },
    };
    if (commanders.some((card) => card.chooses_color)) deck.commander_color_choices = commanders.filter((card) => card.chooses_color)
      .map((card) => ({ commander_name: card.name, color: chosenColor }));
    const externalUrl = `https://edhpowerlevel.com/?${new URLSearchParams({ d: `${main.map(text).join("~")}~~${commandRows.map(text).join("~")}~Z~` })}`;
    return { ...tier, total_usd: unpriced.length ? null : cents / 100, priced_subtotal_usd: cents / 100, unpriced_cards: unpriced,
      report: { deck, external_url: externalUrl, external_rating: { status: "unverified", deck_sha256: hash }, synergy: {
        summary: "Generated from the commanders’ Oracle text, legal card pool, and selected Card Kingdom budget.",
        model: "A neural ranker and Oracle rules score possible synergies. The search also accounts for mana, interaction, and card advantage.",
        catalog_updated_at: manifest.oracle_updated_at,
      } } };
  } catch (error) {
    if (!(error instanceof BudgetUnavailable)) throw error;
    return { ...tier, unavailable_reason: error.message, total_usd: null };
  }
}

export async function buildCommanderBudgets(catalog, commanders, chosenColor, model, manifest) {
  const colors = commanderColors(commanders, chosenColor);
  const keys = new Set(commanders.map((card) => card.name));
  const pool = catalog.filter((card) => !keys.has(card.name) && !(card.color_mask & ~colors) && !(card.land && (landTypes(card) & ~colors)));
  const prepared = prepareSpells(pool.filter((card) => !card.land), commanders, model);
  const landCount = Math.max(...commanders.map((card) => card.cmc)) >= 5 ? 37 : 36;
  const tiers = [];
  const curveGap = (score) => Math.max(0, 72.5 - score, score - 77.5);
  for (const spec of tierSpecs) {
    const initial = await assembleTier(spec, commanders, pool, prepared, colors, landCount, model, manifest, chosenColor);
    const rate = initial.report?.deck.mana_diagnostics.local_on_curve_estimate;
    if (!Number.isFinite(rate) || !curveGap(rate)) { tiers.push(initial); continue; }
    const alternate = await assembleTier(spec, commanders, pool, prepared, colors, landCount + (rate < 72.5 ? 2 : -2), model, manifest, chosenColor);
    const nextRate = alternate.report?.deck.mana_diagnostics.local_on_curve_estimate;
    tiers.push(Number.isFinite(nextRate) && curveGap(nextRate) < curveGap(rate) ? alternate : initial);
  }
  return { schema_version: 1, pricing: manifest.pricing, playability_target: { minimum: 72.5, maximum: 77.5 }, tiers };
}
