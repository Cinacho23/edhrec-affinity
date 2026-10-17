import { useId } from "react";

export default function CumulativeTagFilter({ filter, onChange = filter.setQuery }) {
  const id = useId();
  return (
    <section className="cumulative-tag-filter panel" aria-label="Cumulative tag filter">
      <label htmlFor={id}>Tags to combine</label>
      <div className="cumulative-tag-filter__input">
        <input
          id={id}
          type="search"
          list={`${id}-tags`}
          value={filter.query}
          placeholder="Elves, Good Stuff"
          aria-describedby={`${id}-help`}
          onChange={(event) => onChange(event.target.value)}
        />
        {filter.active && <button type="button" onClick={() => onChange("")}>Clear tags</button>}
      </div>
      <datalist id={`${id}-tags`}>
        {filter.tags.map((tag) => <option key={tag.tag_slug} value={tag.tag_name || tag.tag_slug} />)}
      </datalist>
      <p id={`${id}-help`} className="muted">
        Separate tag names with commas or “and”. Every selected tag must have z &gt; 1.
        Matching commanders sort by the sum of those z-scores, highest first.
      </p>
      <div role="status" aria-live="polite">
        {filter.loading && <p className="muted">Loading selected tag scores…</p>}
        {filter.active && !filter.loading && !filter.error && filter.unmatched.length > 0 && (
          <p className="muted">Choose complete tag names. Unknown tags: {filter.unmatched.join(", ")}.</p>
        )}
        {filter.active && !filter.loading && !filter.error && filter.unmatched.length === 0 && (
          <p className="muted">Combining {filter.selectedTags.map((tag) => tag.tag_name || tag.tag_slug).join(" + ")}.</p>
        )}
        {filter.error && <p className="error-message">Could not load tag filters: {filter.error} <button type="button" onClick={filter.retry}>Retry</button></p>}
      </div>
    </section>
  );
}
