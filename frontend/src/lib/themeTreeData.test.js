import assert from "node:assert/strict";
import test from "node:test";
import { createThemeTreeDataLoader } from "./themeTreeData.js";

const tags = Array.from({ length: 10 }, (_, index) => ({ tag_slug: `tag-${index}` }));

test("loads only selected complete tag files once with a shared concurrency limit across graph requests", async () => {
  const calls = [];
  let active = 0;
  let maximum = 0;
  const loader = createThemeTreeDataLoader({ concurrency: 2, dataSource: {
    loadTagIndex: async () => { calls.push("index"); return tags; },
    loadTagDetail: async (slug) => {
      calls.push(slug);
      active += 1;
      maximum = Math.max(maximum, active);
      await new Promise((resolve) => setTimeout(resolve, 2));
      active -= 1;
      return [{ commander_slug: "example", tag_slug: slug, z: 2 }];
    },
  } });
  assert.equal(loader.loadIndex(), loader.loadIndex());
  await loader.loadIndex();
  const [first, second] = await Promise.all([
    loader.loadTagRows(tags.slice(0, 5)),
    loader.loadTagRows(tags.slice(3, 8)),
  ]);
  assert.deepEqual([...first.keys()], tags.slice(0, 5).map((tag) => tag.tag_slug));
  assert.deepEqual([...second.keys()], tags.slice(3, 8).map((tag) => tag.tag_slug));
  assert.equal(first.get("tag-3"), second.get("tag-3"));
  assert.ok(maximum <= 2);
  await loader.loadTagRows([tags[0], tags[0], tags[7]]);
  assert.deepEqual(calls, ["index", ...tags.slice(0, 8).map((tag) => tag.tag_slug)]);
});

test("failed and malformed requests can be retried while successful tag data remains cached", async () => {
  const attempts = new Map();
  const loader = createThemeTreeDataLoader({ dataSource: {
    loadTagIndex: async () => tags,
    loadTagDetail: async (slug) => {
      const attempt = (attempts.get(slug) || 0) + 1;
      attempts.set(slug, attempt);
      if (slug === "tag-1" && attempt === 1) throw new Error("Offline");
      if (slug === "tag-1" && attempt === 2) return {};
      return [];
    },
  } });
  await assert.rejects(loader.loadTagRows(tags.slice(0, 2)), /Offline/);
  await assert.rejects(loader.loadTagRows(tags.slice(0, 2)), /list of tag rows/);
  assert.deepEqual(await loader.loadTagRows(tags.slice(0, 2)), new Map([["tag-0", []], ["tag-1", []]]));
  assert.equal(attempts.get("tag-0"), 1);
  assert.equal(attempts.get("tag-1"), 3);
});

test("empty selections need no files and invalid limits fail immediately", async () => {
  const loader = createThemeTreeDataLoader({ dataSource: {} });
  assert.deepEqual(await loader.loadTagRows([]), new Map());
  for (const concurrency of [0, -1, 1.5, NaN]) {
    assert.throws(() => createThemeTreeDataLoader({ concurrency }), /positive integer/);
  }
});
