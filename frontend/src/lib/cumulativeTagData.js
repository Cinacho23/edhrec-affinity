import { loadTagDetail, loadTagIndex } from "./api.js";
import { mapWithConcurrency } from "./themeBracketData.js";
import { buildCumulativeTagScores } from "./cumulativeTagUtils.js";

export function createCumulativeTagLoader(source = { loadTagDetail, loadTagIndex }) {
  const requests = new Map();
  function request(key, fetchRows) {
    if (!requests.has(key)) {
      requests.set(key, Promise.resolve().then(fetchRows).then((rows) => {
        if (!Array.isArray(rows)) throw new Error("Expected a list of tag rows.");
        return rows;
      }).catch((error) => {
        requests.delete(key);
        throw error;
      }));
    }
    return requests.get(key);
  }
  return {
    loadIndex: () => request("index", () => source.loadTagIndex()),
    async loadScores(tags) {
      const groups = await mapWithConcurrency(tags, 6, async (tag) => ({
        ...tag,
        rows: await request(`tag:${tag.tag_slug}`, () => source.loadTagDetail(tag.tag_slug)),
      }));
      return buildCumulativeTagScores(groups);
    },
  };
}

export const cumulativeTagLoader = createCumulativeTagLoader();
