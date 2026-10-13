import assert from "node:assert/strict";
import test from "node:test";

import { getCommanderCards, loadCommanderCards } from "./commanderPreview.js";
import { groupRowsByCommander, normalizeAffinityRow } from "./commanderUtils.js";

const card = (name, image, uri = `https://scryfall.com/card/test/${encodeURIComponent(name)}`) => ({
  name,
  scryfall_uri: uri,
  faces: [{ name, image_url: image }],
});

function jsonResponse(data, status = 200) {
  return {
    ok: status === 200,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
}

function apiCard(name, image, extra = {}) {
  return { object: "card", name, scryfall_uri: `https://scryfall.com/card/test/${encodeURIComponent(name)}`, image_uris: { normal: image }, ...extra };
}

test("structured partner and background metadata retains all faces with the right card links", () => {
  const front = { name: "Front", image_url: "front.jpg" };
  const back = { name: "Back", image_url: "back.jpg" };
  const leader = { ...card("Front // Back", "unused"), faces: [front, back, front] };
  const background = card("Background", "background.jpg");
  const row = { commander_cards: [leader, background, leader], card_image_url: "old.jpg" };
  assert.deepEqual(getCommanderCards(row), [
    { ...leader, faces: [front, back] },
    background,
  ]);
  assert.equal(row.commander_cards[0].faces.length, 3, "normalization does not mutate source data");
});

test("legacy partners preserve commas, positions, links and only one primary image", () => {
  const row = {
    commander_name: "Akiri, Line-Slinger // Tymna the Weaver (Commander)",
    card_image_url: "akiri.jpg",
    partner_card_image_urls: ["akiri.jpg", "tymna.jpg"],
    scryfall_uri: "akiri-link",
    partner_scryfall_uris: ["akiri-link", "tymna-link"],
    scryfall_card_names: ["Akiri, Line-Slinger", "Tymna the Weaver"],
  };
  assert.deepEqual(getCommanderCards(row), [
    card("Akiri, Line-Slinger", "akiri.jpg", "akiri-link"),
    card("Tymna the Weaver", "tymna.jpg", "tymna-link"),
  ]);
  assert.deepEqual(getCommanderCards({
    ...row,
    card_image_url: null,
    partner_card_image_urls: [null, "tymna.jpg"],
  }).map((result) => [result.name, result.faces[0]?.image_url]), [
    ["Akiri, Line-Slinger", undefined], ["Tymna the Weaver", "tymna.jpg"],
  ]);
});

test("legacy double-faced names stay one card, and scalar names are never comma-separated", () => {
  const row = { commander_name: "Esika, God of the Tree", scryfall_card_names: "Esika, God of the Tree // The Prismatic Bridge", card_image_url: "front.jpg" };
  assert.equal(getCommanderCards(row).length, 1);
  assert.equal(getCommanderCards(row)[0].name, "Esika, God of the Tree // The Prismatic Bridge");
  assert.equal(getCommanderCards(row)[0].faces[0].name, "Esika, God of the Tree");
  assert.deepEqual(normalizeAffinityRow(row).scryfall_card_names, ["Esika, God of the Tree // The Prismatic Bridge"]);
  assert.deepEqual(getCommanderCards(null), []);
});

test("grouped commander rows carry structured metadata even when the first tag row lacks it", () => {
  const commander_cards = [card("Leader", "leader.jpg"), card("Background", "background.jpg")];
  const rows = [{ commander_slug: "leader", commander_name: "Leader" }, { commander_slug: "leader", commander_cards }];
  assert.deepEqual(groupRowsByCommander(rows)[0].commander_cards, commander_cards);
});

test("complete structured previews need no requests", async (t) => {
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => { requests += 1; return jsonResponse({}, 404); });
  const commander_cards = [card("Structured leader", "leader.jpg"), card("Structured background", "background.jpg")];
  assert.deepEqual(await loadCommanderCards({ commander_cards }), commander_cards);
  assert.equal(requests, 0);
});

test("complete structured partner aliases remain two cards without extra lookups", async (t) => {
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => { requests += 1; return jsonResponse({}, 404); });
  const commander_cards = [
    card("Bjorna, Nightfall Alchemist", "complete-bjorna.jpg"),
    card("Sophina, Spearsage Deserter", "complete-sophina.jpg"),
  ];
  const row = { commander_name: "Bjorna, Nightfall Alchemist // Chief Jim Hopper (Commander)", commander_cards };
  assert.deepEqual(getCommanderCards(row), commander_cards);
  assert.deepEqual(await loadCommanderCards(row), commander_cards);
  assert.equal(requests, 0);
});

test("partial Bjorna/Hopper legacy and structured metadata retains and resolves the missing partner", async (t) => {
  const bjorna = "Bjorna, Nightfall Alchemist";
  const hopper = "Chief Jim Hopper";
  const row = {
    commander_name: `${bjorna} // ${hopper} (Commander)`,
    scryfall_card_names: [bjorna],
    card_image_url: "bjorna.jpg",
    partner_card_image_urls: ["bjorna.jpg"],
    scryfall_uri: "bjorna-link",
  };
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    const name = new URL(url).searchParams.get("exact");
    calls.push(name);
    if (name.includes("//")) return jsonResponse({ object: "error" }, 404);
    return jsonResponse(apiCard(name, name === bjorna ? "bjorna.jpg" : "hopper.jpg"));
  });
  assert.deepEqual(getCommanderCards(row).map((item) => item.name), [bjorna, hopper]);
  assert.equal(getCommanderCards(row)[1].faces.length, 0);
  const loaded = await loadCommanderCards(row);
  assert.deepEqual(loaded.map((item) => [item.name, item.faces[0].image_url]), [[bjorna, "bjorna.jpg"], [hopper, "hopper.jpg"]]);
  assert.equal(calls[0], `${bjorna} // ${hopper}`, "the full name is checked before assuming separate cards");
  const structured = { commander_name: row.commander_name, commander_cards: [card(bjorna, "bjorna.jpg")] };
  assert.deepEqual((await loadCommanderCards(structured)).map((item) => item.name), [bjorna, hopper]);
  const reversed = { ...row, commander_name: `${hopper} // ${bjorna}` };
  assert.deepEqual(getCommanderCards(reversed).map((item) => [item.name, item.faces[0]?.image_url]), [[hopper, undefined], [bjorna, "bjorna.jpg"]]);
});

test("a partial DFC front name resolves the full card before looking up a supposed second commander", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(new URL(url).searchParams.get("exact"));
    return jsonResponse(apiCard("Partial front // Partial back", null, {
      card_faces: [{ name: "Partial front", image_uris: { normal: "partial-front.jpg" } }, { name: "Partial back", image_uris: { normal: "partial-back.jpg" } }],
    }));
  });
  const loaded = await loadCommanderCards({ commander_name: "Partial front // Partial back", scryfall_card_names: ["Partial front"], card_image_url: "partial-front.jpg" });
  assert.equal(loaded.length, 1);
  assert.equal(loaded[0].faces.length, 2);
  assert.deepEqual(calls, ["Partial front // Partial back"]);
});

test("missing partner aliases fall back to cached standalone commander metadata after an API 404", async (t) => {
  const name = "Dustin, Gadget Genius";
  const canonical = "Hargilde, Kindly Runechanter";
  const id = "66666666-6666-4666-8666-666666666666";
  const canonicalImage = `https://cards.scryfall.io/normal/front/6/6/${id}.jpg`;
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    if (url === "/data/latest/commanders/index.json") return jsonResponse([{
      commander_name: `${name} (Commander)`,
      commander_slug: "dustin-gadget-genius",
      scryfall_card_names: [canonical],
      card_image_url: canonicalImage,
    }]);
    if (url.endsWith(id)) return jsonResponse(apiCard(canonical, canonicalImage));
    return jsonResponse({ object: "error" }, 404);
  });
  const commander_cards = [card("Bjorna, Nightfall Alchemist", "alias-bjorna.jpg")];
  const loaded = await loadCommanderCards({ commander_name: `Bjorna, Nightfall Alchemist // ${name}`, commander_cards });
  assert.deepEqual(loaded.map((item) => item.name), ["Bjorna, Nightfall Alchemist", canonical]);
  assert.equal(loaded[1].faces[0].image_url, canonicalImage);
  assert.ok(calls.indexOf(`https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}`) < calls.indexOf("/data/latest/commanders/index.json"));
  await loadCommanderCards({ commander_name: `Other leader // ${name}`, commander_cards: [card("Other leader", "other-leader.jpg")] });
  assert.equal(calls.filter((url) => url === "/data/latest/commanders/index.json").length, 1);
});

test("legacy meld fronts fetch only the meld result and keep the commander's name and link", async (t) => {
  const frontId = "33333333-3333-4333-8333-333333333333";
  const backId = "44444444-4444-4444-8444-444444444444";
  const otherId = "55555555-5555-4555-8555-555555555555";
  const image = `https://cards.scryfall.io/normal/front/3/3/${frontId}.jpg`;
  const all_parts = [
    { id: frontId, component: "meld_part", uri: `https://api.scryfall.com/cards/${frontId}` },
    { id: otherId, component: "meld_part", uri: `https://api.scryfall.com/cards/${otherId}` },
    { id: backId, component: "meld_result", uri: `https://api.scryfall.com/cards/${backId}` },
  ];
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    const id = url.split("/").at(-1);
    assert.notEqual(id, otherId, "the other meld material is not a commander preview");
    const name = id === frontId ? "Gisela, the Broken Blade" : "Brisela, Voice of Nightmares";
    return jsonResponse(apiCard(name, id === frontId ? image : "brisela.jpg", { id, layout: "meld", all_parts }));
  });
  const loaded = await loadCommanderCards({ commander_name: "Gisela, the Broken Blade", scryfall_card_names: ["Gisela, the Broken Blade"], card_image_url: image });
  assert.equal(loaded.length, 1);
  assert.equal(loaded[0].name, "Gisela, the Broken Blade");
  assert.equal(loaded[0].scryfall_uri, "https://scryfall.com/card/test/Gisela%2C%20the%20Broken%20Blade");
  assert.deepEqual(loaded[0].faces.map((face) => face.name), ["Gisela, the Broken Blade", "Brisela, Voice of Nightmares"]);
  assert.deepEqual(calls, [`https://api.scryfall.com/cards/${frontId}`, `https://api.scryfall.com/cards/${backId}`]);
});

test("legacy double-faced lookup uses the image UUID, keeps both sides, coalesces and caches", async (t) => {
  const id = "11111111-1111-4111-8111-111111111111";
  const front = `https://cards.scryfall.io/normal/front/1/1/${id}.jpg`;
  const back = `https://cards.scryfall.io/normal/back/1/1/${id}.jpg`;
  const row = { commander_name: "Hydrated front", scryfall_card_names: ["Hydrated front // Hydrated back"], card_image_url: front };
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return jsonResponse(apiCard("Hydrated front // Hydrated back", null, {
      card_faces: [
        { name: "Hydrated front", image_uris: { normal: front } },
        { name: "Hydrated back", image_uris: { normal: back } },
      ],
    }));
  });
  const pending = loadCommanderCards(row);
  assert.equal(loadCommanderCards(row), pending);
  const results = await pending;
  assert.deepEqual(results[0].faces, [{ name: "Hydrated front", image_url: front }, { name: "Hydrated back", image_url: back }]);
  assert.deepEqual(await loadCommanderCards({ ...row }), results);
  assert.deepEqual(calls, [`https://api.scryfall.com/cards/${id}`]);
});

test("sparse theme rows load local detail and retain both commander/background cards", async (t) => {
  const commander_cards = [card("Local leader", "local.jpg"), card("Local background", "local-background.jpg")];
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return jsonResponse([{ commander_slug: "local-pair", commander_cards }]);
  });
  assert.deepEqual(await loadCommanderCards({ commander_slug: "local-pair", commander_name: "Local leader // Local background" }), commander_cards);
  assert.deepEqual(calls, ["/data/latest/commanders/local-pair.json"]);
});

test("an ambiguous full name is resolved before splitting, so a double-faced card is not a partner", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    return jsonResponse(apiCard("Ambiguous front // Ambiguous back", null, {
      card_faces: [{ name: "Ambiguous front", image_uris: { normal: "a-front.jpg" } }, { name: "Ambiguous back", image_uris: { normal: "a-back.jpg" } }],
    }));
  });
  const cards = await loadCommanderCards({ commander_name: "Ambiguous front // Ambiguous back (Commander)" });
  assert.equal(cards.length, 1);
  assert.equal(cards[0].faces.length, 2);
  assert.deepEqual(calls, ["https://api.scryfall.com/cards/named?exact=Ambiguous%20front%20%2F%2F%20Ambiguous%20back"]);
});

test("missing partner/background metadata splits only after full-name lookup fails", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    calls.push(url);
    const name = new URL(url).searchParams.get("exact");
    if (name.includes("//")) return jsonResponse({ object: "error" }, 404);
    return jsonResponse(apiCard(name, `${name}.jpg`));
  });
  const cards = await loadCommanderCards({ commander_name: "Lookup leader // Lookup background (Commander)" });
  assert.deepEqual(cards.map((result) => result.name), ["Lookup leader", "Lookup background"]);
  assert.equal(calls.length, 3);
});

test("Scryfall requests from simultaneous hovers are serialized and spaced", async (t) => {
  const starts = [];
  let active = 0;
  let maxActive = 0;
  t.mock.method(globalThis, "fetch", async (url) => {
    starts.push(Date.now());
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise((resolve) => setTimeout(resolve, 15));
    active -= 1;
    const name = new URL(url).searchParams.get("exact");
    return jsonResponse(apiCard(name, `${name}.jpg`));
  });
  await Promise.all([loadCommanderCards({ commander_name: "Queue first" }), loadCommanderCards({ commander_name: "Queue second" })]);
  assert.equal(maxActive, 1);
  assert.equal(starts.length, 2);
  assert.ok(starts[1] - starts[0] >= 100);
});

test("offline/error responses retain available art, cache failures and do not poison later lookups", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url) => {
    calls += 1;
    if (url.includes("Recovered")) return jsonResponse(apiCard("Recovered", "recovered.jpg"));
    throw new Error("Offline");
  });
  const row = { commander_name: "Offline front // Offline back", card_image_url: "offline.jpg" };
  assert.deepEqual(await loadCommanderCards(row), getCommanderCards(row));
  const failedCalls = calls;
  assert.deepEqual(await loadCommanderCards(row), getCommanderCards(row));
  assert.equal(calls, failedCalls);
  assert.equal((await loadCommanderCards({ commander_name: "Recovered" }))[0].faces[0].image_url, "recovered.jpg");
  assert.deepEqual(await loadCommanderCards({ commander_slug: "missing-local", commander_name: "Missing local" }), [{ name: "Missing local", scryfall_uri: null, faces: [] }]);
});
