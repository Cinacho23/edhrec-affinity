import { buildThemeTreeNodes, rankThemeTreeNodes } from "./themeTreeUtils.js";

// Full combination graphs grow exponentially. Calculate them off the UI thread
// so users can still change their selection and cancel an obsolete computation.
self.addEventListener("message", ({ data }) => {
  try {
    const graph = buildThemeTreeNodes(data.tags);
    self.postMessage({
      graph: { ...graph, nodes: rankThemeTreeNodes(graph.nodes, data.rowsByTag) },
    });
  } catch (error) {
    self.postMessage({ error: error.message || "Could not generate the complete graph." });
  }
});
