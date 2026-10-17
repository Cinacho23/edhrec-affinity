import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { formatDecimal, formatNumber } from "../lib/formatters";
import { themeTreeDataLoader } from "../lib/themeTreeData";
import {
  THEME_TREE_MIN_TAG_DECKS,
  THEME_TREE_MIN_TOTAL_DECKS,
  combineThemeTreeSubsets,
  getThemeTreeSubsetCount,
  themeSubsetId,
} from "../lib/themeTreeUtils";
import "./ThemeTreePage.css";

const EMPTY_ROWS = new Map();
const EMPTY_GRAPH = { nodes: [], edges: [], rootId: null };
const NODE_WIDTH = 246;
const NODE_HEIGHT = 142;
const ROW_GAP = 76;
const COLUMN_GAP = 28;
const PADDING = 42;

function layoutGraph(nodes) {
  const levels = new Map();
  for (const node of nodes) {
    if (!levels.has(node.tagSlugs.length)) levels.set(node.tagSlugs.length, []);
    levels.get(node.tagSlugs.length).push(node);
  }
  const orderedLevels = [...levels.entries()].sort(([a], [b]) => b - a);
  const maxCount = Math.max(1, ...orderedLevels.map(([, level]) => level.length));
  const width = maxCount * (NODE_WIDTH + COLUMN_GAP) - COLUMN_GAP + PADDING * 2;
  const positions = new Map();
  orderedLevels.forEach(([size, level], index) => {
    const rowWidth = level.length * (NODE_WIDTH + COLUMN_GAP) - COLUMN_GAP;
    level.forEach((node, column) => positions.set(node.id, {
      x: (width - rowWidth) / 2 + column * (NODE_WIDTH + COLUMN_GAP),
      y: PADDING + index * (NODE_HEIGHT + ROW_GAP),
      size,
    }));
  });
  return {
    positions,
    width,
    height: Math.max(1, orderedLevels.length) * (NODE_HEIGHT + ROW_GAP) - ROW_GAP + PADDING * 2,
  };
}

function clampZoom(value) {
  return Math.max(Number.EPSILON, Math.min(2, value));
}

function TreeCanvas({ nodes, edges, selectedIds, onSelect, loading, focusRequest }) {
  const viewportRef = useRef(null);
  const dragRef = useRef(null);
  const arrowId = useId().replaceAll(":", "");
  const [size, setSize] = useState({ width: 800, height: 640 });
  const [camera, setCamera] = useState(null);
  const [dragging, setDragging] = useState(false);
  const layout = useMemo(() => layoutGraph(nodes), [nodes]);
  const fitScale = clampZoom(Math.min((size.width - 32) / layout.width, (size.height - 32) / layout.height, 1));
  const fitCamera = {
    scale: fitScale,
    x: (size.width - layout.width * fitScale) / 2,
    y: (size.height - layout.height * fitScale) / 2,
  };
  const view = camera || fitCamera;
  const viewRef = useRef(view);

  useEffect(() => { viewRef.current = view; }, [view]);

  useEffect(() => {
    const viewport = viewportRef.current;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const viewport = viewportRef.current;
    function wheel(event) {
      event.preventDefault();
      const current = viewRef.current;
      if (event.ctrlKey || event.metaKey) {
        const rect = viewport.getBoundingClientRect();
        const px = event.clientX - rect.left;
        const py = event.clientY - rect.top;
        const scale = clampZoom(current.scale * Math.exp(-event.deltaY * 0.006));
        setCamera({ scale, x: px - (px - current.x) * scale / current.scale, y: py - (py - current.y) * scale / current.scale });
      } else {
        setCamera({ ...current, x: current.x - (event.shiftKey ? event.deltaY : event.deltaX), y: current.y - (event.shiftKey ? 0 : event.deltaY) });
      }
    }
    viewport.addEventListener("wheel", wheel, { passive: false });
    return () => viewport.removeEventListener("wheel", wheel);
  }, []);

  useEffect(() => {
    if (!focusRequest?.id) return;
    const point = layout.positions.get(focusRequest.id);
    if (!point) return;
    const frame = requestAnimationFrame(() => setCamera({
      scale: 1,
      x: size.width / 2 - point.x - NODE_WIDTH / 2,
      y: size.height / 2 - point.y - NODE_HEIGHT / 2,
    }));
    return () => cancelAnimationFrame(frame);
  }, [focusRequest, layout, size]);

  function zoom(factor) {
    const scale = clampZoom(view.scale * factor);
    setCamera({ scale, x: size.width / 2 - (size.width / 2 - view.x) * scale / view.scale, y: size.height / 2 - (size.height / 2 - view.y) * scale / view.scale });
  }

  function startPan(event) {
    if (event.button !== 0 || event.target.closest("button")) return;
    dragRef.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, camera: view };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  }

  function movePan(event) {
    const drag = dragRef.current;
    if (!drag || drag.pointer !== event.pointerId) return;
    setCamera({ ...drag.camera, x: drag.camera.x + event.clientX - drag.x, y: drag.camera.y + event.clientY - drag.y });
  }

  function stopPan() {
    dragRef.current = null;
    setDragging(false);
  }

  return (
    <section className="theme-tree-canvas" aria-label="Theme combination graph">
      <div className="theme-tree-canvas__toolbar">
        <span>Full combination above · single themes below</span>
        <div className="theme-tree-zoom">
          <button type="button" onClick={() => zoom(1 / 1.3)} aria-label="Zoom out">−</button>
          <output aria-label="Zoom level">{view.scale < 0.01 ? (view.scale * 100).toPrecision(2) : Math.round(view.scale * 100)}%</output>
          <button type="button" onClick={() => zoom(1.3)} aria-label="Zoom in">+</button>
          <button type="button" onClick={() => setCamera(null)}>Fit graph</button>
          <button type="button" onClick={() => setCamera({ scale: 1, x: (size.width - layout.width) / 2, y: 28 })}>Reset view</button>
        </div>
      </div>
      <div
        ref={viewportRef}
        className={`theme-tree-viewport${dragging ? " is-dragging" : ""}`}
        tabIndex={0}
        aria-label="Pan graph with arrow keys, drag, or scroll. Use plus and minus to zoom. Tab to select nodes."
        onPointerDown={startPan}
        onPointerMove={movePan}
        onPointerUp={stopPan}
        onPointerCancel={stopPan}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          const moves = { ArrowLeft: [80, 0], ArrowRight: [-80, 0], ArrowUp: [0, 80], ArrowDown: [0, -80] };
          if (moves[event.key]) {
            event.preventDefault();
            setCamera({ ...view, x: view.x + moves[event.key][0], y: view.y + moves[event.key][1] });
          } else if (event.key === "+" || event.key === "=") { event.preventDefault(); zoom(1.3); }
          else if (event.key === "-") { event.preventDefault(); zoom(1 / 1.3); }
          else if (event.key === "0") { event.preventDefault(); setCamera(null); }
        }}
      >
        <div className="theme-tree-stage" style={{ width: layout.width, height: layout.height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
          <svg className="theme-tree-edges" width={layout.width} height={layout.height} aria-hidden="true">
            <defs><marker id={arrowId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
            {edges.map((edge) => {
              const source = layout.positions.get(edge.source);
              const target = layout.positions.get(edge.target);
              if (!source || !target) return null;
              const upper = source.y < target.y ? source : target;
              const lower = source.y < target.y ? target : source;
              const middleY = (upper.y + NODE_HEIGHT + lower.y) / 2;
              const active = selectedIds.includes(edge.source) || selectedIds.includes(edge.target);
              return <path key={`${edge.source}:${edge.target}`} className={active ? "is-active" : ""} d={`M${lower.x + NODE_WIDTH / 2},${lower.y} C${lower.x + NODE_WIDTH / 2},${middleY} ${upper.x + NODE_WIDTH / 2},${middleY} ${upper.x + NODE_WIDTH / 2},${upper.y + NODE_HEIGHT}`} markerEnd={`url(#${arrowId})`} />;
            })}
          </svg>
          {nodes.map((node) => {
            const position = layout.positions.get(node.id);
            const selected = selectedIds.includes(node.id);
            return <button
              type="button"
              key={node.id}
              className={`theme-tree-node${selected ? " is-selected" : ""}${!node.winner && !loading ? " is-empty" : ""}`}
              style={{ left: position.x, top: position.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
              aria-pressed={selected}
              aria-label={`${node.label}: ${loading ? "loading" : node.winner?.commander_name || node.winner?.commander_slug || "No qualifying commander"}${node.winner && !loading ? `, cumulative z-score ${formatDecimal(node.winner.cumulative_z)}` : ""}`}
              onClick={() => onSelect(node.id)}
            >
              <span className="theme-tree-node__meta">{node.tagSlugs.length === 1 ? "Single theme" : `${node.tagSlugs.length} themes`}{selected && <span>Selected</span>}</span>
              <strong>{loading ? "Loading commanders…" : node.winner?.commander_name || node.winner?.commander_slug || "No qualifying commander"}</strong>
              <span className="theme-tree-node__themes" title={node.label}>{node.label}</span>
              <span className="theme-tree-node__score">{loading ? "Calculating scores" : node.winner ? `Σ z ${formatDecimal(node.winner.cumulative_z)}` : "No match meets all minimums"}</span>
            </button>;
          })}
        </div>
      </div>
      <p className="theme-tree-canvas__hint">Drag or scroll to pan · Ctrl/⌘ + scroll to zoom · select up to two nodes to combine</p>
    </section>
  );
}

export default function ThemeTreePage() {
  const [index, setIndex] = useState({ tags: [], loading: true, error: null });
  const [selectedSlugs, setSelectedSlugs] = useState([]);
  const [query, setQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState([]);
  const [focusRequest, setFocusRequest] = useState(null);
  const [result, setResult] = useState({ key: null, rows: EMPTY_ROWS, error: null });
  const [computed, setComputed] = useState({ key: null, graph: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const [addToNode, setAddToNode] = useState("");
  const initialized = useRef(false);
  const searchId = useId();
  const tagBySlug = useMemo(() => new Map(index.tags.map((tag) => [tag.tag_slug, tag])), [index.tags]);
  const selectedTags = useMemo(() => selectedSlugs.map((slug) => tagBySlug.get(slug)).filter(Boolean), [selectedSlugs, tagBySlug]);
  const selectionKey = JSON.stringify(selectedSlugs);
  const dataKey = `${selectionKey}:${attempt}`;

  useEffect(() => {
    let current = true;
    themeTreeDataLoader.loadIndex().then((tags) => {
      if (!current) return;
      setIndex({ tags, loading: false, error: null });
      if (!initialized.current) {
        initialized.current = true;
        const preferred = ["elves", "goodstuff", "wolves"].map((name) => tags.find((tag) => String(tag.tag_slug).replace(/[-_\s]/g, "").toLowerCase() === name));
        setSelectedSlugs((preferred.every(Boolean) ? preferred : tags.slice(0, 3)).map((tag) => tag.tag_slug));
      }
    }, (error) => { if (current) setIndex({ tags: [], loading: false, error: error.message }); });
    return () => { current = false; };
  }, [attempt]);

  useEffect(() => {
    if (selectedTags.length === 0) return;
    let current = true;
    themeTreeDataLoader.loadTagRows(selectedTags).then(
      (rows) => { if (current) setResult({ key: dataKey, rows, error: null }); },
      (error) => { if (current) setResult({ key: dataKey, rows: EMPTY_ROWS, error: error.message }); }
    );
    return () => { current = false; };
  }, [selectedTags, dataKey]);

  useEffect(() => {
    if (selectedTags.length === 0 || result.key !== dataKey || result.error) return;
    let current = true;
    let worker;
    try {
      worker = new Worker(new URL("../lib/themeTreeWorker.js", import.meta.url), { type: "module" });
      worker.onmessage = ({ data }) => {
        if (current) setComputed({ key: dataKey, graph: data.graph || null, error: data.error || null });
        worker.terminate();
      };
      worker.onerror = (event) => {
        event.preventDefault();
        if (current) setComputed({ key: dataKey, graph: null, error: event.message || "Could not generate the complete graph." });
        worker.terminate();
      };
      worker.postMessage({ tags: selectedTags, rowsByTag: result.rows });
    } catch (error) {
      // Worker setup and structured-clone failures are asynchronous UI errors.
      queueMicrotask(() => {
        if (current) setComputed({ key: dataKey, graph: null, error: error.message });
      });
      worker?.terminate();
    }
    return () => { current = false; worker?.terminate(); };
  }, [selectedTags, result, dataKey]);

  const loadingData = selectedTags.length > 0 && result.key !== dataKey;
  const error = index.error || (result.key === dataKey ? result.error : null) || (computed.key === dataKey ? computed.error : null);
  const buildingGraph = selectedTags.length > 0 && !loadingData && !error && computed.key !== dataKey;
  const loading = loadingData || buildingGraph;
  const graph = !loading && !error && computed.key === dataKey ? computed.graph || EMPTY_GRAPH : EMPTY_GRAPH;
  const nodes = graph.nodes;
  const totalPossibleNodes = getThemeTreeSubsetCount(selectedTags.length);
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const activeNode = nodeById.get(selectedIds.at(-1)) || nodeById.get(graph.rootId);
  const searchMatches = index.tags.filter((tag) => !selectedSlugs.includes(tag.tag_slug) && `${tag.tag_name} ${tag.tag_slug}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 12);
  const themesToAdd = activeNode ? selectedTags.filter((tag) => !activeNode.tagSlugs.includes(tag.tag_slug)) : [];
  const pair = selectedIds.map((id) => nodeById.get(id)).filter(Boolean);

  function changeThemes(slugs) {
    setSelectedSlugs(slugs);
    setSelectedIds([]);
    setFocusRequest(null);
    setAddToNode("");
  }

  function addTheme(tag) {
    changeThemes([...selectedSlugs, tag.tag_slug]);
    setQuery("");
  }

  function focusCombination(slugs) {
    if (slugs?.length) {
      const id = themeSubsetId(slugs);
      setSelectedIds([id]);
      setFocusRequest({ id, token: Date.now() });
      setAddToNode("");
    }
  }

  function selectNode(id) {
    setSelectedIds((previous) => previous.includes(id) ? previous.filter((selected) => selected !== id) : [...previous.slice(-1), id]);
    setAddToNode("");
  }

  return <section className="page theme-tree-page">
    <div className="page-header theme-tree-heading">
      <p className="eyebrow">Explore shared affinities</p>
      <h1>Theme Tree</h1>
      <p>Find the leading commander for every combination of your themes. Each node adds its themes’ z-scores. Commanders need at least <strong>{THEME_TREE_MIN_TOTAL_DECKS} total decks</strong>, with at least <strong>{THEME_TREE_MIN_TAG_DECKS} decks and a z-score above 1 in every theme</strong> included in the node.</p>
    </div>

    <section className="theme-tree-picker panel" aria-labelledby="tree-themes-title">
      <div className="theme-tree-picker__heading"><h2 id="tree-themes-title">Your themes</h2><span className="muted">{selectedTags.length} selected</span></div>
      <div className="theme-tree-chips">
        {selectedTags.map((tag) => <button type="button" key={tag.tag_slug} onClick={() => changeThemes(selectedSlugs.filter((slug) => slug !== tag.tag_slug))} aria-label={`Remove ${tag.tag_name || tag.tag_slug}`}><span>{tag.tag_name || tag.tag_slug}</span><span aria-hidden="true">×</span></button>)}
        {selectedTags.length === 0 && <p className="muted">Add themes to start exploring.</p>}
      </div>
      <label htmlFor={searchId}>Add a theme</label>
      <form className="theme-tree-search" onSubmit={(event) => { event.preventDefault(); if (searchMatches[0]) addTheme(searchMatches[0]); }}>
        <input id={searchId} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search any theme, e.g. Artifacts" disabled={index.loading} />
        <button type="submit" disabled={searchMatches.length === 0 || index.loading}>Add theme</button>
      </form>
      {query.trim() && <div className="theme-tree-suggestions" aria-label="Matching themes">
        {searchMatches.map((tag) => <button type="button" key={tag.tag_slug} onClick={() => addTheme(tag)}>{tag.tag_name || tag.tag_slug}<span aria-hidden="true"> +</span></button>)}
        {!index.loading && searchMatches.length === 0 && <p className="muted">No unselected themes match “{query}”.</p>}
      </div>}
      <div role="status" aria-live="polite">
        {index.loading && <p className="muted">Loading available themes…</p>}
        {loadingData && <p className="muted">Loading commander scores for {selectedTags.length} themes…</p>}
        {buildingGraph && <p className="muted">Generating all {BigInt(totalPossibleNodes).toLocaleString("en-US")} combinations… You can change themes while this runs.</p>}
        {error && <p className="error-message">Could not load the complete theme graph: {error} <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button></p>}
      </div>
    </section>

    {selectedTags.length > 0 && <>
      <div className="theme-tree-summary">
        <div><strong>{formatNumber(nodes.length)} visible nodes</strong><span> of {BigInt(totalPossibleNodes).toLocaleString("en-US")} combinations</span></div>
        <p>Every nonempty combination is included automatically, including empty results. There is no theme cutoff. Each added theme roughly doubles the graph, so large selections take more time and memory.</p>
      </div>
      <div className="theme-tree-workspace">
        <TreeCanvas key={selectionKey} nodes={nodes} edges={graph.edges} selectedIds={selectedIds} onSelect={selectNode} loading={loading || Boolean(error)} focusRequest={focusRequest} />
        <aside className="theme-tree-detail panel" aria-label="Selected combination">
          <div className="theme-tree-detail__heading"><p className="eyebrow">{selectedIds.length ? "Selected combination" : "Full combination"}</p><span className="theme-tree-count">{activeNode?.tagSlugs.length || 0} themes</span></div>
          {activeNode && <>
            <h2>{loading || error ? "Waiting for scores" : activeNode.winner?.commander_name || activeNode.winner?.commander_slug || "No qualifying commander"}</h2>
            <p className="theme-tree-detail__themes">{activeNode.label}</p>
            {!loading && !error && activeNode.winner && <>
              <div className="theme-tree-total"><span>Cumulative z-score</span><strong>{formatDecimal(activeNode.winner.cumulative_z)}</strong></div>
              <dl className="theme-tree-breakdown">{activeNode.winner.cumulative_tag_scores.map((tag) => <div key={tag.tag_slug}><dt>{tag.tag_name || tag.tag_slug}</dt><dd>{formatDecimal(tag.z)}</dd></div>)}</dl>
              <Link className="theme-tree-commander-link" to={`/commanders/${activeNode.winner.commander_slug}`}>View commander →</Link>
            </>}
            {!loading && !error && <p className="muted">{formatNumber(activeNode.qualifyingCommanderCount)} commanders have at least {THEME_TREE_MIN_TOTAL_DECKS} total decks, with at least {THEME_TREE_MIN_TAG_DECKS} decks and z &gt; 1 in every theme in this node.</p>}
            <div className="theme-tree-detail__actions">
              <button type="button" onClick={() => setFocusRequest({ id: activeNode.id, token: Date.now() })}>Focus this node</button>
            </div>
            <section className="theme-tree-combine">
              <h3>Combine two nodes</h3>
              <p className="muted">Select two nodes to focus their combined themes.</p>
              <button type="button" disabled={pair.length !== 2} onClick={() => focusCombination(combineThemeTreeSubsets(pair[0], pair[1]))}>Combine selected ({pair.length}/2)</button>
            </section>
            {activeNode.tagSlugs.length > 1 && <section className="theme-tree-edit-node">
              <h3>Explore without a theme</h3>
              <div className="theme-tree-subset-buttons">{activeNode.tags.map((tag) => <button type="button" key={tag.tag_slug} onClick={() => focusCombination(activeNode.tagSlugs.filter((slug) => slug !== tag.tag_slug))}>− {tag.tag_name || tag.tag_slug}</button>)}</div>
            </section>}
            {themesToAdd.length > 0 && <section className="theme-tree-edit-node">
              <h3>Add to this combination</h3>
              <label htmlFor={`${searchId}-node`}>Theme from your selection</label>
              <select id={`${searchId}-node`} value={themesToAdd.some((tag) => tag.tag_slug === addToNode) ? addToNode : ""} onChange={(event) => setAddToNode(event.target.value)}>
                <option value="">Choose a theme</option>
                {themesToAdd.map((tag) => <option key={tag.tag_slug} value={tag.tag_slug}>{tag.tag_name || tag.tag_slug}</option>)}
              </select>
              <button type="button" disabled={!themesToAdd.some((tag) => tag.tag_slug === addToNode)} onClick={() => focusCombination([...activeNode.tagSlugs, addToNode])}>Show combination</button>
            </section>}
          </>}
        </aside>
      </div>
    </>}
  </section>;
}
