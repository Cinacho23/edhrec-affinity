import { useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getCommanderCards, loadCommanderCards } from "../lib/commanderPreview.js";

function CardFace({ face, label, loading }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="commander-preview__face">
      {face.image_url && !failed ? (
        <img src={face.image_url} alt={label} onError={() => setFailed(true)} />
      ) : (
        <div className="commander-preview__missing">{loading && !face.image_url ? "Loading card…" : "Card image unavailable"}</div>
      )}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

export default function CommanderPreview({ commander, anchorRef, popupRef, id, onClose, onPointerEnter, onPointerLeave }) {
  const [cards, setCards] = useState(() => getCommanderCards(commander));
  const [loading, setLoading] = useState(true);
  const faces = cards.flatMap((card) => (card.faces.length ? card.faces : [{ name: card.name }]).map((face, index) => ({
    ...face,
    label: card.faces.length > 1
      ? `${face.name || card.name} (${index === 0 ? "front" : "back"})`
      : face.name || card.name,
  })));

  useEffect(() => {
    let active = true;
    loadCommanderCards(commander)
      .then((result) => { if (active) setCards(result); })
      .catch(() => { /* Keep any exported images when enrichment is unavailable. */ })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [commander]);

  useLayoutEffect(() => {
    const popup = popupRef.current;
    const anchor = anchorRef.current;
    if (!popup || !anchor) return;
    const margin = 12;
    const gap = 10;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = window.innerHeight;
      if (rect.bottom < 0 || rect.top > viewportHeight || rect.right < 0 || rect.left > viewportWidth) {
        onClose();
        return;
      }
      popup.style.maxHeight = `${Math.max(80, viewportHeight - margin * 2)}px`;
      const { width, height } = popup.getBoundingClientRect();
      let left = Math.max(margin, Math.min(rect.left, viewportWidth - width - margin));
      const below = rect.bottom + gap;
      const above = rect.top - gap - height;
      const top = below + height <= viewportHeight - margin
        ? below
        : above >= margin ? above : Math.max(margin, viewportHeight - height - margin);
      if (below + height > viewportHeight - margin && above < margin) {
        // Prefer beside the name when neither vertical side has enough room.
        if (rect.right + gap + width <= viewportWidth - margin) left = rect.right + gap;
        else if (rect.left - gap - width >= margin) left = rect.left - gap - width;
      }
      popup.style.left = `${left}px`;
      popup.style.top = `${top}px`;
      popup.style.visibility = "visible";
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(popup);
    const onScroll = (event) => {
      if (!popup.contains(event.target)) place();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [anchorRef, popupRef, cards, loading, onClose]);

  useEffect(() => {
    const onKeyDown = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={popupRef}
      id={id}
      role="tooltip"
      className="commander-preview"
      style={{ width: Math.max(220, Math.min(760, faces.length * 188 + 12)) }}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
    >
      <div className="commander-preview__title">{commander.commander_name || commander.commander || "Commander"}</div>
      {faces.length > 0 ? (
        <div className="commander-preview__faces">
          {faces.map((face, index) => <CardFace key={`${face.image_url}-${index}`} face={face} label={face.label} loading={loading} />)}
        </div>
      ) : (
        <p className="commander-preview__status">{loading ? "Loading card preview…" : "Card preview unavailable."}</p>
      )}
      {loading && faces.length > 0 && <p className="commander-preview__status">Loading card details…</p>}
    </div>,
    document.body
  );
}
