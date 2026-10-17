import test from "node:test";
import assert from "node:assert/strict";
import { createCumulativeTagLoader } from "./cumulativeTagData.js";

const tags = [{ tag_slug: "elves", tag_name: "Elves" }, { tag_slug: "good-stuff", tag_name: "Good Stuff" }];

test("loads complete selected tag files once, sharing requests across combinations", async () => {
  const calls = [];
  const loader = createCumulativeTagLoader({
    loadTagIndex: async () => { calls.push("index"); return tags; },
    loadTagDetail: async (slug) => {
      calls.push(slug);
      return [{ commander_slug: "example", tag_slug: slug, z: slug === "elves" ? 12.12 : 9.12 }];
    },
  });
  await Promise.all([loader.loadIndex(), loader.loadIndex()]);
  const [both, one] = await Promise.all([loader.loadScores(tags), loader.loadScores([tags[0]])]);
  assert.ok(Math.abs(both.get("example").cumulative_z - 21.24) < 1e-12);
  assert.equal(one.get("example").cumulative_z, 12.12);
  assert.deepEqual(calls, ["index", "elves", "good-stuff"]);
});

test("failed or invalid tag data fails the combination and can be retried", async () => {
  let attempts = 0;
  const loader = createCumulativeTagLoader({ loadTagDetail: async () => {
    attempts += 1;
    if (attempts === 1) throw new Error("Offline");
    if (attempts === 2) return {};
    return [{ commander_slug: "example", tag_slug: "elves", z: 2 }];
  } });
  await assert.rejects(loader.loadScores([tags[0]]), /Offline/);
  await assert.rejects(loader.loadScores([tags[0]]), /list of tag rows/);
  assert.equal((await loader.loadScores([tags[0]])).get("example").cumulative_z, 2);
});
