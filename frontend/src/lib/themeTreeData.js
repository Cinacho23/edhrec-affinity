import { loadTagDetail, loadTagIndex } from "./api.js";

/** Cache complete tag files and share one request limit across every graph load. */
export function createThemeTreeDataLoader({
  concurrency = 6,
  dataSource = { loadTagDetail, loadTagIndex },
} = {}) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError("Concurrency must be a positive integer.");
  }
  const requests = new Map();
  const queue = [];
  let active = 0;

  function startNext() {
    while (active < concurrency && queue.length > 0) {
      const { fetchRows, resolve, reject } = queue.shift();
      active += 1;
      Promise.resolve().then(fetchRows).then(resolve, reject).finally(() => {
        active -= 1;
        startNext();
      });
    }
  }

  function request(key, fetchRows) {
    if (!requests.has(key)) {
      const promise = new Promise((resolve, reject) => {
        queue.push({ fetchRows, resolve, reject });
        startNext();
      }).then((rows) => {
        if (!Array.isArray(rows)) throw new Error(`Expected a list of tag rows for ${key}.`);
        return rows;
      }).catch((error) => {
        requests.delete(key);
        throw error;
      });
      requests.set(key, promise);
    }
    return requests.get(key);
  }

  return {
    loadIndex: () => request("index", () => dataSource.loadTagIndex()),
    async loadTagRows(selectedTags) {
      const slugs = [...new Set(selectedTags.map((tag) => tag.tag_slug))];
      const pairs = await Promise.all(slugs.map(async (slug) => [
        slug, await request(`tag:${slug}`, () => dataSource.loadTagDetail(slug)),
      ]));
      return new Map(pairs);
    },
  };
}

export const themeTreeDataLoader = createThemeTreeDataLoader();
