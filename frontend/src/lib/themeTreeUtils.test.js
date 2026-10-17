import assert from "node:assert/strict";
import test from "node:test";
import {
  buildThemeTreeNodes,
  combineThemeTreeSubsets,
  getThemeTreeChildren,
  getThemeTreeSubsetCount,
  rankThemeTreeNodes,
  themeSubsetId,
} from "./themeTreeUtils.js";

function tag(tag_slug) {
  return { tag_slug, tag_name: tag_slug.replaceAll("-", " ") };
}

function row(commander_slug, tag_slug, z, extra = {}) {
  return { commander_slug, commander_name: commander_slug, total_decks: 200, tag_slug, z, ...extra };
}

function assertReducedAndConnected(graph) {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const supersets = new Map(graph.nodes.map((node) => [node.id, []]));
  for (const edge of graph.edges) {
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    assert.ok(source.tagSlugs.length < target.tagSlugs.length);
    assert.ok(source.tagSlugs.every((slug) => target.tagSlugs.includes(slug)));
    assert.equal(graph.nodes.some((middle) =>
      middle.tagSlugs.length > source.tagSlugs.length && middle.tagSlugs.length < target.tagSlugs.length &&
      source.tagSlugs.every((slug) => middle.tagSlugs.includes(slug)) &&
      middle.tagSlugs.every((slug) => target.tagSlugs.includes(slug))
    ), false, "No edge may bypass a visible intermediate subset");
    supersets.get(edge.source).push(edge.target);
  }
  function reachesRoot(id, visited = new Set()) {
    if (id === graph.rootId) return true;
    if (visited.has(id)) return false;
    visited.add(id);
    return supersets.get(id).some((next) => reachesRoot(next, visited));
  }
  for (const node of graph.nodes) assert.ok(reachesRoot(node.id), `${node.id} reaches root`);
}

test("three themes create all seven combinations and nine immediate-subset edges", () => {
  const selected = [tag("wolves"), tag("elves"), tag("good-stuff")];
  const graph = buildThemeTreeNodes(selected);
  assert.equal(graph.nodes.length, 7);
  assert.equal(graph.edges.length, 9);
  assert.equal(graph.totalPossibleNodes, "7");
  assert.equal(graph.fullyExpanded, true);
  assert.equal(graph.rootId, '["elves","good-stuff","wolves"]');
  assert.deepEqual(graph.nodes.map((node) => node.tagSlugs.length), [1, 1, 1, 2, 2, 2, 3]);
  assert.deepEqual(buildThemeTreeNodes([...selected].reverse()), graph);
  assertReducedAndConnected(graph);
});

test("up to seven themes materialize every subset with canonical identities", () => {
  const tags = Array.from({ length: 7 }, (_, index) => tag(`theme-${index}`));
  const graph = buildThemeTreeNodes([...tags, tags[0]], [["theme-1", "theme-0", "theme-1"]]);
  assert.equal(graph.nodes.length, 127);
  assert.equal(graph.edges.length, 441);
  assert.equal(graph.totalPossibleNodes, "127");
  assert.equal(themeSubsetId(["b", "a", "a"]), '["a","b"]');
  assert.equal(graph.fullyExpanded, true);
});

test("large graphs preserve expansions and unions while every node remains connected to the root", () => {
  const tags = Array.from({ length: 8 }, (_, index) => tag(`theme-${index}`));
  const initial = buildThemeTreeNodes(tags);
  assert.equal(initial.nodes.length, 15);
  assert.equal(initial.totalPossibleNodes, "255");
  assert.equal(initial.fullyExpanded, false);
  const left = initial.nodes.find((node) => node.id === themeSubsetId(["theme-6"]));
  const right = initial.nodes.find((node) => node.id === themeSubsetId(["theme-7"]));
  const union = combineThemeTreeSubsets(left, right);
  assert.deepEqual(union, ["theme-6", "theme-7"]);
  const root = initial.nodes.find((node) => node.id === initial.rootId);
  const children = getThemeTreeChildren(root);
  assert.equal(children.length, 8);
  const expanded = buildThemeTreeNodes(tags, [union, ...children, ["theme-1", "theme-4", "theme-7"]]);
  for (const subset of [union, ...children, ["theme-1", "theme-4", "theme-7"]]) {
    assert.ok(expanded.nodes.some((node) => node.id === themeSubsetId(subset)));
  }
  assert.deepEqual(getThemeTreeChildren(left), []);
  assertReducedAndConnected(initial);
  assertReducedAndConnected(expanded);
});

test("theme selection has no fixed hard cap and exact possible counts exceed Number precision", () => {
  const graph = buildThemeTreeNodes(Array.from({ length: 80 }, (_, index) => tag(`theme-${String(index).padStart(2, "0")}`)));
  assert.equal(graph.nodes.length, 159);
  assert.equal(graph.totalPossibleNodes, "1208925819614629174706175");
  assert.equal(graph.nodes.at(-1).tagSlugs.length, 80);
  assert.equal(getThemeTreeSubsetCount(0), "0");
  assert.throws(() => getThemeTreeSubsetCount(-1), /non-negative integer/);
  assert.deepEqual(buildThemeTreeNodes([]), {
    nodes: [], edges: [], totalPossibleNodes: "0", fullyExpanded: true, rootId: null,
  });
});

test("each combination gets its own highest cumulative winner with strict z > 1 membership", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("good-stuff"), tag("wolves")]);
  const rowsByTag = new Map([
    ["elves", [row("example", "elves", 12.12), row("all-three", "elves", 5), row("boundary", "elves", 1)]],
    ["good-stuff", [row("example", "good-stuff", 9.12), row("all-three", "good-stuff", 10), row("boundary", "good-stuff", 100)]],
    ["wolves", [row("example", "wolves", 0.59), row("all-three", "wolves", 2), row("boundary", "wolves", 4)]],
  ]);
  const original = JSON.stringify([...rowsByTag]);
  const ranked = rankThemeTreeNodes(graph.nodes, rowsByTag);
  const byId = new Map(ranked.map((node) => [node.id, node]));
  const pair = byId.get(themeSubsetId(["elves", "good-stuff"]));
  assert.equal(pair.winner.commander_slug, "example");
  assert.ok(Math.abs(pair.winner.cumulative_z - 21.24) < 1e-12);
  assert.equal(pair.qualifyingCommanderCount, 2);
  assert.equal(byId.get(graph.rootId).winner.commander_slug, "all-three");
  assert.equal(byId.get(graph.rootId).winner.cumulative_z, 17);
  assert.equal(byId.get(graph.rootId).qualifyingCommanderCount, 1);
  assert.equal(byId.get(themeSubsetId(["good-stuff", "wolves"])).winner.commander_slug, "boundary");
  assert.deepEqual(pair.winner.cumulative_tag_scores.map((value) => value.z), [12.12, 9.12]);
  assert.equal(JSON.stringify([...rowsByTag]), original);
  assert.ok(graph.nodes.every((node) => !Object.hasOwn(node, "winner")));
});

test("empty combinations remain visible and missing, invalid, or legacy scores never qualify", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("wolves")]);
  const rowsByTag = new Map([
    ["elves", [row("only-elves", "elves", 5), row("legacy", "elves", null, { legacy_z: 10 })]],
    ["wolves", [
      row("only-wolves", "wolves", 7),
      ...[null, undefined, "", " ", false, NaN, Infinity, -3, 0, 1].map((z) => row("only-elves", "wolves", z)),
      row("only-elves", "incorrect-tag", 99),
      row("legacy", "wolves", 3),
    ]],
  ]);
  const ranked = rankThemeTreeNodes(graph.nodes, rowsByTag);
  assert.equal(ranked.length, 3);
  const root = ranked.find((node) => node.id === graph.rootId);
  assert.equal(root.winner, null);
  assert.equal(root.qualifyingCommanderCount, 0);
  assert.throws(() => rankThemeTreeNodes(graph.nodes, new Map()), /Missing complete tag data/);
});

test("ranking uses unrounded sums, then total decks, name and slug, and deduplicates repeated tag rows", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("wolves")]);
  const candidates = [
    ["zulu", "Zulu", 200, 2],
    ["beta", "Alpha", 200, 2],
    ["alpha", "Alpha", 200, 2],
    ["more-decks", "More", 300, 2],
    ["precise", "Precise", 100, 2.0000000001],
  ];
  const rowsByTag = new Map(["elves", "wolves"].map((slug) => [slug, candidates.map(([id, name, decks, z]) =>
    row(id, slug, z, { commander_name: name, total_decks: decks })
  )]));
  rowsByTag.get("elves").push(row("precise", "elves", 1.5));
  const winner = () => rankThemeTreeNodes(graph.nodes, rowsByTag).find((node) => node.id === graph.rootId).winner;
  assert.equal(winner().commander_slug, "precise");
  for (const slug of rowsByTag.keys()) rowsByTag.set(slug, rowsByTag.get(slug).filter((value) => value.commander_slug !== "precise"));
  assert.equal(winner().commander_slug, "more-decks");
  for (const slug of rowsByTag.keys()) rowsByTag.set(slug, rowsByTag.get(slug).filter((value) => value.commander_slug !== "more-decks").reverse());
  assert.equal(winner().commander_slug, "alpha");
});
