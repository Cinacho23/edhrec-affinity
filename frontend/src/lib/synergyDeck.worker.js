import { buildCommanderDeck, commanderCards, commanderColors, maskForColors } from "./synergyDeckBuilder.js";
import { decodeCardFeatures } from "./synergyDeckRanker.js";

async function readJson(url, compressed = false) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`The Synergy Deck data returned HTTP ${response.status}.`);
  if (!compressed) return response.json();
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes[0] !== 31 || bytes[1] !== 139) return JSON.parse(new TextDecoder().decode(bytes));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).json();
}

function assetUrl(base, path, revision) {
  const url = new URL(path, base);
  if (revision) url.searchParams.set("v", revision);
  return url;
}

async function readCatalog(base, manifest, kinds, model, cache) {
  const slices = manifest.shards.filter((shard) => !(shard.color_mask & ~kinds));
  return (await Promise.all(slices.map(async (shard) => {
    if (!cache.has(shard.path)) {
      const pending = readJson(assetUrl(base, shard.path, manifest.revision), true).then((chunk) => {
        const features = decodeCardFeatures(chunk.features, model?.feature_scale, model?.feature_count, chunk.cards.length);
        return chunk.cards.map((card, index) => ({ ...card, features: card.has_model_features === false ? undefined : features[index] }));
      });
      cache.set(shard.path, pending);
    }
    return cache.get(shard.path);
  }))).flat();
}

self.addEventListener("message", async ({ data }) => {
  const { id, commanders: names, colorIdentity, chosenColor, baseUrl } = data;
  try {
    const manifest = await readJson(assetUrl(baseUrl, "manifest.json"));
    if (manifest.schema_version !== 1) throw new Error("The Synergy Deck data version is unsupported.");
    const model = manifest.model_path ? await readJson(assetUrl(baseUrl, manifest.model_path, manifest.revision)) : null;
    const cache = new Map();
    const mask = Array.isArray(colorIdentity) ? maskForColors(colorIdentity) | maskForColors(chosenColor || "") : 31;
    let catalog = await readCatalog(baseUrl, manifest, mask, model, cache);
    let commanders = commanderCards(names, catalog);
    if (commanders.some((card) => !card) && mask !== 31) {
      catalog = await readCatalog(baseUrl, manifest, 31, model, cache);
      commanders = commanderCards(names, catalog);
    }
    const allColors = commanderColors(commanders, chosenColor);
    if (allColors & ~mask) catalog = await readCatalog(baseUrl, manifest, allColors, model, cache);
    self.postMessage({ id, report: await buildCommanderDeck(catalog, commanders, chosenColor, model, manifest) });
  } catch (error) {
    self.postMessage({ id, error: error instanceof Error ? error.message : "Could not generate a deck for this commander." });
  }
});
