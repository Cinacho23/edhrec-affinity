import assert from "node:assert/strict";
import { test } from "node:test";
import { buildCommanderBudgets, commanderCards, commanderColors, exactDeckHash } from "./synergyDeckBuilder.js";

const offer = (price_cents, stock = 1) => ({ price_cents, stock, printing: "Test Set" });
const base = (name, extra = {}) => ({ name, type_line: "Creature", oracle_text: "", mana_cost: "{2}",
  cmc: 2, color_mask: 1, produced_mask: 0, offers: [offer(20)], ...extra });

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

test("generated tiers verify exact quantities, color limits, and retail ceilings", async () => {
  const commander = base("Commander", { can_command: true, offers: [offer(1200)] });
  const plains = base("Plains", { land: true, basic: true, produced_mask: 1, type_line: "Basic Land — Plains",
    offers: [offer(15, 12), offer(25, 30)] });
  const staples = Array.from({ length: 95 }, (_, index) => base(`Common ${index}`));
  const otherColor = base("Illegal color", { color_mask: 2 });
  const rare = base("Rare", { offers: [offer(10000)] });
  const bundle = await buildCommanderBudgets([commander, plains, ...staples, otherColor, rare], [commander], undefined, null, {});
  const tier = bundle.tiers[0], { deck, external_rating } = tier.report;
  assert.ok(tier.total_usd <= 100);
  assert.equal(deck.cards.reduce((sum, card) => sum + card.quantity, 0), 100);
  assert.equal(deck.cards.find((card) => card.name === "Plains").purchase_options.length, 2);
  assert.ok(deck.cards.every(({ name }) => !["Illegal color", "Rare"].includes(name)));
  assert.equal(deck.deck_sha256, await exactDeckHash(deck.cards.toReversed()));
  assert.equal(external_rating.status, "unverified");
  const expensive = { ...commander, offers: [offer(12500)] };
  const highBudget = await buildCommanderBudgets([expensive, plains, ...staples], [expensive], undefined, null, {});
  assert.match(highBudget.tiers[0].unavailable_reason, /commanders consume/);
  assert.ok(highBudget.tiers[1].report.deck.card_count === 100);
});
