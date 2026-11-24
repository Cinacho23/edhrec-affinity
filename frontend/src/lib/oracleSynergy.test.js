import test from "node:test";
import assert from "node:assert/strict";
import { commanderOracleProfile, oracleProfile, oracleSynergy } from "./oracleSynergy.js";

const card = (name, oracle_text, type_line = "Sorcery", cmc = 2) => ({ name, oracle_text, type_line, cmc });
const evaluate = (commander, candidate, coverage) => oracleSynergy(commanderOracleProfile([commander]), oracleProfile(candidate), coverage);

test("rewards counter sources and counter payoffs in the correct direction", () => {
  const commander = card("Sovereign", "Whenever one or more +1/+1 counters are put on a creature you control, draw a card.", "Legendary Creature — Elf");
  const counters = card("Growth", "Put two +1/+1 counters on target creature.");
  const answer = card("Counterspell", "Counter target spell.", "Instant");
  assert.ok(evaluate(commander, counters).matches.some((match) => match.id === "plus-counter"));
  assert.ok(evaluate(commander, counters).score > evaluate(commander, answer).score);
  assert.ok(evaluate(commander, counters, new Map([["plus-counter", 10]])).score < evaluate(commander, counters).score);
});

test("distinguishes tribal effects from unrelated creature types and abilities", () => {
  const commander = card("Lathril, Blade of the Elves", "Whenever Lathril deals combat damage to a player, create that many 1/1 green Elf Warrior creature tokens. Tap ten untapped Elves you control: Each opponent loses 10 life and you gain 10 life. Other Elves you control get +1/+1.", "Legendary Creature — Elf Noble");
  const elf = card("Llanowar Elves", "{T}: Add {G}.", "Creature — Elf Druid", 1);
  const wolf = card("Young Wolf", "Undying", "Creature — Wolf", 1);
  assert.ok(evaluate(commander, elf).matches.some((match) => match.id === "kind:elf"));
  assert.ok(evaluate(commander, elf).score > evaluate(commander, wolf).score);
});

test("joins complementary actions across both mana colors, zones and card faces", () => {
  const commander = card("Chrononaut", "Whenever you attack, exile the top card of your library and put three time counters on it. If it doesn't have suspend, it gains suspend.", "Legendary Creature — Time Lord");
  const timer = card("Clockspinning", "Choose a counter on target permanent or suspended card. Remove that counter from that permanent or card or put another of those counters on it.", "Instant");
  assert.ok(evaluate(commander, timer).matches.some((match) => match.id === "time-advance"));
  const payoff = { name: "Exile Reward // Reverse", card_faces: [card("Exile Reward", "Flying", "Creature — Bird"),
    card("Reverse", "Whenever you cast a spell from exile, draw a card.", "Enchantment")] };
  const other = card("Rose", "Whenever you cast a spell from exile, draw a card.", "Legendary Creature — Human");
  const profile = commanderOracleProfile([commander, other]);
  assert.ok(oracleSynergy(profile, oracleProfile(payoff)).matches.some((match) => match.id === "exile-cast" && match.direction === "supported-by-commanders"));
});

test("does not value handing Food to an opponent as creating your own", () => {
  const commander = card("Chef", "Whenever you sacrifice a Food, draw a card.", "Legendary Creature — Halfling");
  const ownFood = card("Make Food", "Create a Food token.");
  const enemyFood = card("Share Food", "Target opponent creates a Food token.");
  assert.ok(evaluate(commander, ownFood).score > 0);
  assert.equal(evaluate(commander, enemyFood).score, 0);
});
