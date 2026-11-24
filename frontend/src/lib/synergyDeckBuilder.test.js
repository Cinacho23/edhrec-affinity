import assert from "node:assert/strict";
import { test } from "node:test";
import { buildCommanderDeck, commanderCards, commanderColors, exactDeckHash } from "./synergyDeckBuilder.js";

const offer = (price_cents, stock = 1) => ({ price_cents, stock, printing: "Test Set" });
const base = (name, extra = {}) => ({ name, type_line: "Creature", oracle_text: "", mana_cost: "{2}",
  cmc: 2, color_mask: 1, produced_mask: 0, ...extra });

function fixture() {
  const commander = base("Commander", { can_command: true, type_line: "Legendary Creature — Human", oracle_text: "Whenever you create a token, draw a card." });
  const plains = base("Plains", { land: true, basic: true, produced_mask: 1, type_line: "Basic Land — Plains" });
  const snowPlains = { ...plains, name: "Snow-Covered Plains", type_line: "Basic Snow Land — Plains" };
  const staples = Array.from({ length: 95 }, (_, index) => base(`Common ${index}`, { offers: [] }));
  const otherColor = base("Illegal color", { color_mask: 2 });
  const payoff = base("Token Creator", { oracle_text: "Create a 1/1 Human creature token.", offers: [] });
  return [commander, plains, snowPlains, ...staples, otherColor, payoff];
}

test("named aliases and a chosen color apply to the verified command zone", () => {
  const piper = base("The Prismatic Piper", { can_command: true, chooses_color: true, color_mask: 0, aliases: ["Piper"] });
  assert.deepEqual(commanderCards(["piper"], [piper]), [piper]);
  assert.equal(commanderColors([piper], "G"), 16);
  assert.throws(() => commanderColors([piper]), /Choose a pregame color/);
  assert.throws(() => commanderColors([piper, piper], "G"), /command zone/);
  const adventurer = base("Adventurer", { type_line: "Legendary Creature — Human", oracle_text: "Choose a Background" });
  const background = base("Scholar", { type_line: "Legendary Enchantment — Background", color_mask: 2 });
  assert.equal(commanderColors([adventurer, background]), 3);
});

test("an entirely unquoted catalog builds one legal 100-card deck", async () => {
  const catalog = fixture();
  const { deck, external_rating, external_url, playability_target } = await buildCommanderDeck(catalog, [catalog[0]], undefined, null, {});
  assert.equal(deck.cards.reduce((sum, card) => sum + card.quantity, 0), 100);
  assert.ok(deck.cards.some(({ name }) => name === "Token Creator"));
  assert.ok(deck.cards.every(({ name }) => name !== "Illegal color"));
  assert.ok(deck.cards.filter(({ primary_role }) => primary_role === "Spell").every(({ quantity }) => quantity === 1));
  assert.equal(deck.deck_sha256, await exactDeckHash(deck.cards.toReversed()));
  assert.equal(external_rating.status, "unverified");
  assert.equal(external_rating.deck_sha256, deck.deck_sha256);
  assert.ok(new URL(external_url).searchParams.get("d").endsWith("~~1 Commander~Z~"));
  assert.deepEqual(playability_target, { minimum: 72.5, maximum: 77.5 });
});

test("price and stock metadata cannot affect a generated deck or its report", async () => {
  const catalog = fixture();
  const priceNoise = catalog.map((card, index) => ({ ...card, price_cents: 100_000 - index,
    offers: index % 2 ? [offer(0, 0)] : [offer(10_000_000, 1000)] }));
  const input = { oracle_updated_at: "2026-11-23" };
  const plain = await buildCommanderDeck(catalog, [catalog[0]], undefined, null, input);
  const noisy = await buildCommanderDeck(priceNoise, [priceNoise[0]], undefined, null, { ...input, pricing: { source: "Not Used" } });
  assert.deepEqual(noisy, plain);
  assert.doesNotMatch(JSON.stringify(noisy), /price|purchase_options|stock|budget|tiers/i);
});

test("the worker returns one report and no budget wrapper", async (t) => {
  const catalog = fixture();
  let received;
  const messages = [];
  globalThis.self = { addEventListener: (_, listener) => { received = listener; }, postMessage: (data) => messages.push(data) };
  t.after(() => { delete globalThis.self; });
  t.mock.method(globalThis, "fetch", async (url) => {
    const payload = new URL(url).pathname.endsWith("manifest.json")
      ? { schema_version: 1, oracle_updated_at: "2026-11-23", shards: [{ color_mask: 1, path: "cards-01.json.gz" }] }
      : { cards: catalog };
    return Response.json(payload);
  });
  await import("./synergyDeck.worker.js");
  await received({ data: { id: 42, commanders: [catalog[0].name], colorIdentity: ["W"], baseUrl: "https://example.test/synergy-assets/" } });
  assert.equal(messages.length, 1);
  assert.deepEqual(Object.keys(messages[0]).sort(), ["id", "report"]);
  assert.equal(messages[0].id, 42);
  assert.equal(messages[0].report.deck.card_count, 100);
});
