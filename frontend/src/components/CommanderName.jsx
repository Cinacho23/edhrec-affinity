import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import CommanderPreview from "./CommanderPreview.jsx";
import "./CommanderPreview.css";

// Keep the ordinary link behaviour, with the same preview for mouse and keyboard.
export default function CommanderName({ commander, className = "table-commander-link", linked = true }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const popupRef = useRef(null);
  const timer = useRef(null);
  const overPreview = useRef(false);
  const tooltipId = useId();
  const name = commander.commander_name || commander.commander || "Unknown commander";
  const slug = commander.commander_slug || commander.slug;

  const cancelTimer = useCallback(() => window.clearTimeout(timer.current), []);
  const close = useCallback(() => {
    cancelTimer();
    overPreview.current = false;
    setOpen(false);
  }, [cancelTimer]);

  useEffect(() => cancelTimer, [cancelTimer]);

  useEffect(() => {
    if (!open) return;
    // A keyboard-focused name and a different hovered name share one preview.
    const dismissOther = (event) => { if (event.detail !== tooltipId) close(); };
    document.dispatchEvent(new CustomEvent("commander-preview-open", { detail: tooltipId }));
    document.addEventListener("commander-preview-open", dismissOther);
    return () => document.removeEventListener("commander-preview-open", dismissOther);
  }, [open, tooltipId, close]);

  function scheduleOpen(event) {
    if (event.pointerType === "touch") return;
    cancelTimer();
    timer.current = window.setTimeout(() => setOpen(true), 180);
  }

  function scheduleClose() {
    cancelTimer();
    timer.current = window.setTimeout(() => {
      if (!overPreview.current && document.activeElement !== anchorRef.current) {
        setOpen(false);
      }
    }, 140);
  }

  const triggerProps = {
    ref: anchorRef,
    className: `commander-name ${className}`,
    "aria-describedby": open ? tooltipId : undefined,
    onPointerEnter: scheduleOpen,
    onPointerLeave: scheduleClose,
    onFocus: () => {
      cancelTimer();
      setOpen(true);
    },
    onBlur: scheduleClose,
    onClick: close,
    onKeyDown: (event) => {
      const popup = popupRef.current;
      if (!open || !popup) return;
      const steps = { ArrowDown: 48, ArrowUp: -48, PageDown: popup.clientHeight, PageUp: -popup.clientHeight };
      if (event.key in steps) {
        event.preventDefault();
        popup.scrollBy({ top: steps[event.key] });
      }
    },
  };

  return (
    <>
      {linked && slug ? (
        <Link {...triggerProps} to={`/commanders/${encodeURIComponent(slug)}`}>{name}</Link>
      ) : (
        <span {...triggerProps} tabIndex={0}>{name}</span>
      )}
      {open && (
        <CommanderPreview
          key={slug || name}
          commander={commander}
          anchorRef={anchorRef}
          popupRef={popupRef}
          id={tooltipId}
          onClose={close}
          onPointerEnter={() => {
            overPreview.current = true;
            cancelTimer();
          }}
          onPointerLeave={() => {
            overPreview.current = false;
            scheduleClose();
          }}
        />
      )}
    </>
  );
}
