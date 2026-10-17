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
  return { commander_slug, commander_name: commander_slug, total_decks: 200, tag_decks: 5, tag_slug, z, ...extra };
}

function assertReducedAndConnected(graph) {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  assert.equal(nodesById.size, graph.nodes.length, "Every subset has a unique identity");
  const supersets = new Map(graph.nodes.map((node) => [node.id, []]));
  const edgeIds = new Set();
  for (const edge of graph.edges) {
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    assert.ok(source && target, "Both endpoints are visible nodes");
    assert.equal(source.tagSlugs.length + 1, target.tagSlugs.length, "Each edge adds exactly one theme");
    assert.ok(source.tagSlugs.every((slug) => target.tagSlugs.includes(slug)));
    const edgeId = JSON.stringify([edge.source, edge.target]);
    assert.equal(edgeIds.has(edgeId), false, "Every edge is unique");
    edgeIds.add(edgeId);
    supersets.get(edge.source).push(edge.target);
  }
  const reachesRoot = new Set([graph.rootId]);
  const themeCount = nodesById.get(graph.rootId)?.tagSlugs.length || 0;
  for (const node of [...graph.nodes].reverse()) {
    assert.equal(node.id, themeSubsetId(node.tagSlugs), "Every identity is canonical");
    assert.equal(supersets.get(node.id).length, themeCount - node.tagSlugs.length);
    if (supersets.get(node.id).some((next) => reachesRoot.has(next))) reachesRoot.add(node.id);
    assert.ok(reachesRoot.has(node.id), `${node.id} reaches root`);
  }
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

test("duplicate theme selections materialize each canonical subset only once", () => {
  const tags = Array.from({ length: 7 }, (_, index) => tag(`theme-${index}`));
  const graph = buildThemeTreeNodes([...tags, tags[0]]);
  assert.equal(graph.nodes.length, 127);
  assert.equal(graph.edges.length, 441);
  assert.equal(graph.totalPossibleNodes, "127");
  assert.equal(themeSubsetId(["b", "a", "a"]), '["a","b"]');
  assert.equal(graph.fullyExpanded, true);
});

for (const [themeCount, nodeCount, edgeCount] of [[8, 255, 1016], [10, 1023, 5110], [12, 4095, 24564]]) {
  test(`${themeCount} themes automatically show all ${nodeCount} combinations and ${edgeCount} immediate connections`, () => {
    const tags = Array.from({ length: themeCount }, (_, index) => tag(`theme-${index}`));
    const graph = buildThemeTreeNodes(tags);
    assert.equal(graph.nodes.length, nodeCount);
    assert.equal(graph.edges.length, edgeCount);
    assert.equal(graph.totalPossibleNodes, String(nodeCount));
    assert.equal(graph.fullyExpanded, true);
    const layerCounts = new Map();
    for (const node of graph.nodes) {
      layerCounts.set(node.tagSlugs.length, (layerCounts.get(node.tagSlugs.length) || 0) + 1);
    }
    let combinations = 1;
    for (let layer = 1; layer <= themeCount; layer += 1) {
      combinations = combinations * (themeCount - layer + 1) / layer;
      assert.equal(layerCounts.get(layer), combinations, `Layer ${layer} contains every combination`);
    }
    assertReducedAndConnected(graph);
  });
}

test("combined nodes and all immediate children already exist in a full graph", () => {
  const graph = buildThemeTreeNodes(Array.from({ length: 8 }, (_, index) => tag(`theme-${index}`)));
  const left = graph.nodes.find((node) => node.id === themeSubsetId(["theme-6"]));
  const right = graph.nodes.find((node) => node.id === themeSubsetId(["theme-7"]));
  const union = combineThemeTreeSubsets(left, right);
  assert.deepEqual(union, ["theme-6", "theme-7"]);
  const root = graph.nodes.find((node) => node.id === graph.rootId);
  const children = getThemeTreeChildren(root);
  assert.equal(children.length, 8);
  for (const subset of [union, ...children, ["theme-1", "theme-4", "theme-7"]]) {
    assert.ok(graph.nodes.some((node) => node.id === themeSubsetId(subset)));
  }
  assert.deepEqual(getThemeTreeChildren(left), []);
});

test("exact possible counts exceed Number precision without materializing the graph", () => {
  assert.equal(getThemeTreeSubsetCount(80), "1208925819614629174706175");
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

test("leaves and combinations require at least 200 total decks and 5 tag decks, including numeric strings", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("wolves")]);
  const rowsByTag = new Map(["elves", "wolves"].map((slug) => [slug, [
    row("too-few-total", slug, 100, { total_decks: 199 }),
    row("too-few-tag", slug, 100, { tag_decks: 4 }),
    row("exact-minimum", slug, 2),
    row("above-minimum", slug, 3, { total_decks: 201, tag_decks: 6 }),
    row("numeric-strings", slug, 4, { total_decks: "200", tag_decks: "5" }),
  ]]));
  const ranked = rankThemeTreeNodes(graph.nodes, rowsByTag);
  for (const node of ranked) {
    assert.equal(node.qualifyingCommanderCount, 3);
    assert.equal(node.winner.commander_slug, "numeric-strings");
    assert.equal(node.winner.cumulative_z, 4 * node.tagSlugs.length);
  }
});

test("every constituent theme must meet the tag deck minimum and empty combinations remain visible", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("wolves")]);
  const rowsByTag = new Map([
    ["elves", [row("one-low-tag", "elves", 100), row("eligible", "elves", 2)]],
    ["wolves", [row("one-low-tag", "wolves", 100, { tag_decks: 4 }), row("eligible", "wolves", 3)]],
  ]);
  const ranked = rankThemeTreeNodes(graph.nodes, rowsByTag);
  const root = ranked.find((node) => node.id === graph.rootId);
  assert.equal(root.winner.commander_slug, "eligible");
  assert.equal(root.winner.cumulative_z, 5);
  assert.equal(root.qualifyingCommanderCount, 1);
  assert.equal(ranked.find((node) => node.id === themeSubsetId(["elves"])).qualifyingCommanderCount, 2);
  assert.equal(ranked.find((node) => node.id === themeSubsetId(["wolves"])).qualifyingCommanderCount, 1);

  rowsByTag.set("wolves", rowsByTag.get("wolves").filter((value) => value.commander_slug !== "eligible"));
  const emptyRanked = rankThemeTreeNodes(graph.nodes, rowsByTag);
  assert.equal(emptyRanked.length, graph.nodes.length);
  assert.equal(emptyRanked.find((node) => node.id === graph.rootId).winner, null);
  assert.equal(emptyRanked.find((node) => node.id === graph.rootId).qualifyingCommanderCount, 0);
});

test("missing, invalid, and nonfinite deck counts cannot qualify or replace a valid row", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("wolves")]);
  const invalidCounts = [null, undefined, "", " ", false, true, "invalid", NaN, Infinity, -Infinity, "Infinity"];
  const rowsByTag = new Map(["elves", "wolves"].map((slug) => {
    const rows = ["total_decks", "tag_decks"].flatMap((field) => [
      ...invalidCounts.map((value, index) => row(`${field}-${index}`, slug, 100, { [field]: value })),
      row("eligible", slug, 100, { [field]: undefined }),
    ]);
    rows.push(row("eligible", slug, 2));
    return [slug, rows];
  }));
  for (const node of rankThemeTreeNodes(graph.nodes, rowsByTag)) {
    assert.equal(node.qualifyingCommanderCount, 1);
    assert.equal(node.winner.commander_slug, "eligible");
    assert.equal(node.winner.cumulative_z, 2 * node.tagSlugs.length);
  }
});

test("ranking uses unrounded sums, then total decks, name and slug, and deduplicates repeated tag rows", () => {
  const graph = buildThemeTreeNodes([tag("elves"), tag("wolves")]);
  const candidates = [
    ["zulu", "Zulu", 200, 2],
    ["beta", "Alpha", 200, 2],
    ["alpha", "Alpha", 200, 2],
    ["more-decks", "More", 300, 2],
    ["precise", "Precise", 200, 2.0000000001],
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
