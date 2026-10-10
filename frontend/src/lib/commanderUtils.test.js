import assert from "node:assert/strict";
import test from "node:test";

import {
  getVisibleCommanderTagRows,
  MIN_COMMANDER_TAG_DECKS,
} from "./commanderUtils.js";

test("commander tags need at least five tagged decks, including the boundary", () => {
  const rows = [0, 4, 5, 6].map((tag_decks) => ({ tag_decks }));

  assert.equal(MIN_COMMANDER_TAG_DECKS, 5);
  assert.deepEqual(getVisibleCommanderTagRows(rows), [rows[2], rows[3]]);
});

test("commander tags accept numeric strings and exclude invalid or missing counts", () => {
  const visibleRows = [{ tag_decks: "5" }, { tag_decks: " 6 " }];
  const excludedRows = [
    {},
    ...[null, undefined, "", " ", "4", "unknown", NaN, Infinity, -Infinity,
      "Infinity", false, true, [], [5], {}].map((tag_decks) => ({ tag_decks })),
  ];

  assert.deepEqual(
    getVisibleCommanderTagRows([...excludedRows, ...visibleRows]),
    visibleRows
  );
});

test("tag visibility depends on tagged decks independently of total decks or z-score", () => {
  const hidden = { tag_decks: 4, total_decks: 10000, z: 99 };
  const visible = { tag_decks: 5, total_decks: 199, z: null };

  assert.deepEqual(getVisibleCommanderTagRows([hidden, visible]), [visible]);
});

test("filtering keeps input order and original rows without mutating raw data", () => {
  const rows = Object.freeze([
    Object.freeze({ tag_name: "First", tag_decks: 6 }),
    Object.freeze({ tag_name: "Excluded", tag_decks: 4 }),
    Object.freeze({ tag_name: "Last", tag_decks: 5 }),
  ]);
  const visibleRows = getVisibleCommanderTagRows(rows);

  assert.deepEqual(visibleRows, [rows[0], rows[2]]);
  assert.notEqual(visibleRows, rows);
  assert.equal(visibleRows[0], rows[0]);
  assert.equal(visibleRows[1], rows[2]);
  assert.equal(rows.length, 3);
});

test("an all-excluded commander retains its raw metadata row", () => {
  const commander = Object.freeze({
    commander_name: "Example Commander",
    commander_slug: "example-commander",
    total_decks: 300,
    card_image_url: "https://example.test/commander.jpg",
    color_identity: Object.freeze(["U"]),
    tag_decks: 4,
  });
  const rows = Object.freeze([commander]);

  assert.deepEqual(getVisibleCommanderTagRows(rows), []);
  assert.equal(rows[0], commander);
  assert.equal(rows[0].commander_name, "Example Commander");
  assert.equal(rows[0].total_decks, 300);
  assert.equal(rows[0].card_image_url, "https://example.test/commander.jpg");
  assert.deepEqual(rows[0].color_identity, ["U"]);
});
