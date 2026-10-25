import { useEffect, useState } from "react";

import { getCommanderCards, loadCommanderCards } from "../lib/commanderPreview.js";
import CommanderImageGallery from "./CommanderImageGallery.jsx";
import "./BuildDiscoveryCards.css";

export default function BuildDiscoveryCards({ commander }) {
  const [requested, setRequested] = useState(false);
  const [resolution, setResolution] = useState(null);
  const cards = resolution?.source === commander
    ? resolution.cards
    : getCommanderCards(commander);
  const loading = requested && resolution?.source !== commander;
  const thumbnails = cards.map((card) => card.faces[0]).filter((face) => face?.image_url);
  const multiple = cards.length > 1;
  const label = multiple ? "Read commander cards" : "Read commander card";
  const name = commander.commander_name || commander.commander || "";

  useEffect(() => {
    if (!requested) return;
    let active = true;
    loadCommanderCards(commander)
      .then((loaded) => {
        if (active) setResolution({ source: commander, cards: loaded });
      })
      .catch(() => {
        if (active) setResolution({ source: commander, cards: getCommanderCards(commander) });
      });
    return () => { active = false; };
  }, [commander, requested]);

  return <details
    className="build-discovery-cards"
    onToggle={(event) => {
      if (event.currentTarget.open) setRequested(true);
    }}
  >
    <summary aria-label={name ? `${label}: ${name}` : label}>
      {thumbnails.length ? <span className="build-discovery-cards__thumbnails" aria-hidden="true">
        {thumbnails.map((face, index) => <img
          key={`${face.image_url}-${index}`}
          src={face.image_url}
          alt=""
          loading="lazy"
          onError={(event) => { event.currentTarget.style.display = "none"; }}
        />)}
      </span> : null}
      <span className="build-discovery-cards__label">
        <span>{label}</span>
        <span className="build-discovery-cards__toggle" aria-hidden="true" />
      </span>
    </summary>
    <div className="build-discovery-cards__expanded" aria-busy={loading}>
      {requested && thumbnails.length ? <CommanderImageGallery
        commander={{ ...commander, commander_cards: cards }}
        variant="detail"
      /> : null}
      {loading ? <p className="muted" role="status">Loading commander cards…</p>
        : requested && !thumbnails.length ? <p className="muted" role="status">Commander card images are unavailable.</p>
          : requested && cards.some((card) => !card.faces.length) ? <p className="muted" role="status">Some commander card images are unavailable.</p> : null}
    </div>
  </details>;
}
