import { loadBuildCollectorExecution, loadBuildStyleIndex } from "./api.js";

export function watchBuildDiscovery({ onIndex, onExecution, onIndexError,
  loadIndex = loadBuildStyleIndex, loadExecution = loadBuildCollectorExecution,
  visibilityDocument = globalThis.document, intervalMs = 2 * 60 * 1000,
  schedule = globalThis.setInterval, unschedule = globalThis.clearInterval,
}) {
  let active = true;
  let request = null;
  const visible = () => !visibilityDocument || visibilityDocument.visibilityState === "visible";
  async function refresh() {
    if (!active || !visible() || request) return;
    const controller = new AbortController();
    request = controller;
    try {
      const [index, execution] = await Promise.allSettled([
        loadIndex({ signal: controller.signal }), loadExecution({ signal: controller.signal }),
      ]);
      if (!active || request !== controller || controller.signal.aborted) return;
      if (index.status === "fulfilled") onIndex(index.value);
      else if (index.reason?.name !== "AbortError") onIndexError(index.reason);
      // Execution status is optional. Clear an unavailable observation rather
      // than leaving an old "running" badge on screen indefinitely.
      onExecution(execution.status === "fulfilled" ? execution.value : null);
    } finally {
      if (request === controller) request = null;
    }
  }
  function visibilityChanged() {
    if (visible()) void refresh();
    else {
      request?.abort();
      request = null;
    }
  }
  const timer = schedule(() => { void refresh(); }, intervalMs);
  visibilityDocument?.addEventListener("visibilitychange", visibilityChanged);
  void refresh();
  return () => {
    active = false;
    unschedule(timer);
    visibilityDocument?.removeEventListener("visibilitychange", visibilityChanged);
    request?.abort();
    request = null;
  };
}
