import { Link } from "react-router-dom";
import { getCommanderCards } from "../lib/commanderPreview.js";
import "./CommanderImageGallery.css";

export default function CommanderImageGallery({ commander, variant = "card", linked = false, linkTo }) {
  const cards = getCommanderCards(commander);
  const faces = cards.flatMap((card) => card.faces.map((face, index) => ({
    ...face,
    scryfall_uri: card.scryfall_uri,
    label: card.faces.length > 1
      ? `${face.name || card.name} (${index === 0 ? "front" : "back"})`
      : face.name || card.name,
  })));
  const stacked = faces.length === 2 && cards.length === 2 && variant === "card";

  if (faces.length === 0) {
    return <div className="commander-gallery__placeholder">No image available</div>;
  }

  return (
    <div className={`commander-gallery commander-gallery--${variant}${stacked ? " commander-gallery--stacked" : ""}`}>
      {faces.map((face, index) => {
        const image = (
          <img
            className="commander-gallery__image"
            src={face.image_url}
            alt={face.label}
            loading="lazy"
            onError={(event) => {
              if (!event.currentTarget.src.endsWith("/placeholder-card.svg")) {
                event.currentTarget.src = `${import.meta.env.BASE_URL}placeholder-card.svg`;
              }
            }}
          />
        );
        return (
          <figure className="commander-gallery__face" key={`${face.image_url}-${index}`}>
            {linked && linkTo ? (
              <Link to={linkTo}>{image}</Link>
            ) : face.scryfall_uri ? (
              <a href={face.scryfall_uri} target="_blank" rel="noreferrer" aria-label={`Open ${face.label} on Scryfall`}>{image}</a>
            ) : image}
            {variant === "detail" && <figcaption>{face.label}</figcaption>}
          </figure>
        );
      })}
    </div>
  );
}
