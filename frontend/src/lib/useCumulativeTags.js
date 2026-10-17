import { useCallback, useEffect, useMemo, useState } from "react";
import { cumulativeTagLoader } from "./cumulativeTagData.js";
import { applyCumulativeTagScores, resolveCumulativeTags } from "./cumulativeTagUtils.js";

const EMPTY_SCORES = new Map();

export function useCumulativeTags() {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState({ tags: [], loading: true, error: null });
  const [result, setResult] = useState({ key: null, scores: EMPTY_SCORES, error: null });
  const [attempt, setAttempt] = useState(0);
  const active = query.trim() !== "";
  const { selectedTags, unmatched } = useMemo(
    () => resolveCumulativeTags(query, index.tags), [query, index.tags]
  );
  const selectionKey = JSON.stringify(selectedTags.map((tag) => tag.tag_slug));
  const canLoad = active && !index.loading && !index.error && unmatched.length === 0 && selectedTags.length > 0;
  const requestKey = `${selectionKey}:${attempt}`;

  useEffect(() => {
    let current = true;
    cumulativeTagLoader.loadIndex().then(
      (tags) => { if (current) setIndex({ tags, loading: false, error: null }); },
      (error) => { if (current) setIndex({ tags: [], loading: false, error: error.message }); }
    );
    return () => { current = false; };
  }, [attempt]);

  useEffect(() => {
    if (!canLoad) return;
    let current = true;
    cumulativeTagLoader.loadScores(selectedTags).then(
      (scores) => { if (current) setResult({ key: requestKey, scores, error: null }); },
      (error) => { if (current) setResult({ key: requestKey, scores: EMPTY_SCORES, error: error.message }); }
    );
    return () => { current = false; };
  }, [canLoad, selectedTags, requestKey]);

  const scores = canLoad && result.key === requestKey ? result.scores : EMPTY_SCORES;
  const filterRows = useCallback(
    (rows) => active ? applyCumulativeTagScores(rows, scores) : rows,
    [active, scores]
  );
  const retry = () => setAttempt((value) => value + 1);

  return {
    query, setQuery, active, tags: index.tags, selectedTags, unmatched, scores, filterRows, retry,
    loading: active && (index.loading || (canLoad && result.key !== requestKey)),
    error: index.error || (canLoad && result.key === requestKey ? result.error : null),
  };
}
