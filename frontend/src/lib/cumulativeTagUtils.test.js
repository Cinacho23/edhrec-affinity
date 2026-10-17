import test from "node:test";
import assert from "node:assert/strict";
import { applyCumulativeTagScores, buildCumulativeTagScores, resolveCumulativeTags } from "./cumulativeTagUtils.js";
import { sortRows } from "./tableUtils.js";

const tags = [
  { tag_slug: "elves", tag_name: "Elves" },
  { tag_slug: "good-stuff", tag_name: "Good Stuff" },
  { tag_slug: "wolves", tag_name: "Wolves" },
  { tag_slug: "time-counters", tag_name: "Time Counters" },
];
const row = (commander, tag_slug, z, extra = {}) => ({ commander_slug: commander, tag_slug, z, ...extra });
const group = (index, rows) => ({ ...tags[index], rows });

test("resolves complete names/slugs, multiword names and and-separated tags without duplicates", () => {
  for (const query of ["Elves, Good Stuff", "elves and good stuff", "ELVES,good-stuff, elves"]) {
    const result = resolveCumulativeTags(query, tags);
    assert.deepEqual(result.selectedTags.map((tag) => tag.tag_slug), ["elves", "good-stuff"]);
    assert.deepEqual(result.unmatched, []);
  }
  assert.deepEqual(resolveCumulativeTags("time counters", tags).selectedTags, [tags[3]]);
  assert.deepEqual(resolveCumulativeTags("elf, good", tags).unmatched, ["elf", "good"]);
  assert.deepEqual(resolveCumulativeTags("  ", tags), { selectedTags: [], unmatched: [] });
  const namedAnd = { tag_slug: "cats-and-dogs", tag_name: "Cats and Dogs" };
  assert.deepEqual(resolveCumulativeTags("Cats and Dogs", [namedAnd]).selectedTags, [namedAnd]);
});

test("user example sums selected tags and excludes the commander when wolves is added", () => {
  const groups = [group(0, [row("example", "elves", 12.12)]), group(1, [row("example", "good-stuff", 9.12)])];
  const scores = buildCumulativeTagScores(groups);
  assert.ok(Math.abs(scores.get("example").cumulative_z - 21.24) < 1e-12);
  assert.deepEqual(scores.get("example").cumulative_tag_scores.map((tag) => tag.z), [12.12, 9.12]);
  assert.equal(buildCumulativeTagScores([...groups, group(2, [row("example", "wolves", 0.59)])]).size, 0);
});

test("every selected tag must exist with finite canonical z strictly above 1", () => {
  const values = [null, undefined, "", " ", NaN, Infinity, -Infinity, false, 0, -0.92, 0.99, 1, "1", 1.00001, "2"];
  const first = values.map((value, index) => row(String(index), "elves", value, { legacy_z: 99 }));
  first.push(row("missing-second", "elves", 10));
  const second = values.map((_, index) => row(String(index), "good-stuff", 4));
  second.push(row("missing-first", "good-stuff", 10));
  const scores = buildCumulativeTagScores([group(0, first), group(1, second)]);
  assert.deepEqual([...scores.keys()], ["13", "14"]);
});

test("deduplicates tags and rows, ignores unrelated tags, and sorts numeric totals descending", () => {
  const elves = group(0, [row("low", "elves", 2), row("high", "elves", 11), row("high", "elves", 11), row("other", "wolves", 99)]);
  const scores = buildCumulativeTagScores([elves, elves, group(1, [row("high", "good-stuff", 2), row("low", "good-stuff", 2)])]);
  const source = [{ commander_slug: "low", name: "Low" }, { commander_slug: "high" }, { commander_slug: "absent" }];
  const results = sortRows(applyCumulativeTagScores(source, scores), "cumulative_z", "desc");
  assert.deepEqual(results.map((result) => [result.commander_slug, result.cumulative_z]), [["high", 13], ["low", 4]]);
  assert.equal(results[1].name, "Low");
  assert.equal(source[0].cumulative_z, undefined);
  assert.equal(buildCumulativeTagScores([]).size, 0);
});
