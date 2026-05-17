export default function CommanderImageGallery({
  commanderName,
  cardImageUrl,
  partnerCardImageUrls,
  scryfallUri,
  partnerScryfallUris,
  variant = "card",
}) {
  const partnerImages = Array.isArray(partnerCardImageUrls)
    ? partnerCardImageUrls.filter(Boolean)
    : [];

  const allImages = cardImageUrl
    ? [cardImageUrl, ...partnerImages.filter((url) => url !== cardImageUrl)]
    : partnerImages;

  const allUris = Array.isArray(partnerScryfallUris)
    ? [scryfallUri, ...partnerScryfallUris].filter(Boolean)
    : [scryfallUri].filter(Boolean);

  if (allImages.length === 0) {
    return (
      <div className={`commander-image-gallery commander-image-gallery--${variant}`}>
        <div className="commander-image-gallery__placeholder">
          No image available
        </div>
      </div>
    );
  }

  if (allImages.length === 1) {
    return (
      <div
        className={`commander-image-gallery commander-image-gallery--single commander-image-gallery--${variant}`}
      >
        <figure className="commander-image-gallery__figure">
          {allUris[0] ? (
            <a
              className="commander-image-gallery__card"
              href={allUris[0]}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${commanderName} on Scryfall`}
            >
              <img
                className="commander-image-gallery__image"
                src={allImages[0]}
                alt={commanderName}
                loading="lazy"
              />
            </a>
          ) : (
            <img
              className="commander-image-gallery__image"
              src={allImages[0]}
              alt={commanderName}
              loading="lazy"
            />
          )}
        </figure>
      </div>
    );
  }

  return (
    <div
      className={`commander-image-gallery commander-image-gallery--stacked commander-image-gallery--${variant}`}
    >
      <div className="commander-image-gallery__stack">
        <figure className="commander-image-gallery__figure commander-image-gallery__stack-item commander-image-gallery__stack-item--back">
          {allUris[1] ? (
            <a
              className="commander-image-gallery__card"
              href={allUris[1]}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open rear commander card for ${commanderName} on Scryfall`}
            >
              <img
                className="commander-image-gallery__image"
                src={allImages[1]}
                alt={`${commanderName} partner card`}
                loading="lazy"
              />
            </a>
          ) : (
            <img
              className="commander-image-gallery__image"
              src={allImages[1]}
              alt={`${commanderName} partner card`}
              loading="lazy"
            />
          )}
        </figure>

        <figure className="commander-image-gallery__figure commander-image-gallery__stack-item commander-image-gallery__stack-item--front">
          {allUris[0] ? (
            <a
              className="commander-image-gallery__card"
              href={allUris[0]}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open front commander card for ${commanderName} on Scryfall`}
            >
              <img
                className="commander-image-gallery__image"
                src={allImages[0]}
                alt={commanderName}
                loading="lazy"
              />
            </a>
          ) : (
            <img
              className="commander-image-gallery__image"
              src={allImages[0]}
              alt={commanderName}
              loading="lazy"
            />
          )}
        </figure>
      </div>
    </div>
  );
}