/*
  CommanderImageGallery.jsx

  Displays one or more commander card images.

  Display behavior:
  - Single commander:
      Shows one normal card image.

  - Partner / paired commander:
      Shows a stacked two-card layout.
      The front card is shown in front.
      The back card is shifted diagonally up/right, similar to EDHREC-style
      paired commander displays.

  Interaction behavior:
  - Hover the back card:
      The back card moves forward and becomes the active/front card.

  - Keyboard focus:
      The same effect works when tabbing to the back card link/button.

  Why this component exists:
  - The Scryfall enrichment step can store:
      card_image_url
      partner_card_image_urls
      partner_scryfall_uris
      scryfall_card_names

  - The frontend should display both partner cards when those two image URLs
    exist.
*/

const PLACEHOLDER_IMAGE = "/placeholder-card.svg";

function uniqueNonEmpty(values) {
  /*
    Removes:
    - null
    - undefined
    - empty strings
    - duplicate URLs
  */
  const seen = new Set();
  const result = [];

  for (const value of values) {
    if (!value) {
      continue;
    }

    const normalized = String(value).trim();

    if (!normalized || seen.has(normalized)) {
      continue;
    }

    seen.add(normalized);
    result.push(normalized);
  }

  return result;
}

function getCommanderImageEntries(commander) {
  /*
    partner_card_image_urls is the preferred field for paired commanders.

    card_image_url remains useful for:
    - single commanders
    - older enriched rows
    - fallback display
  */
  const partnerImages = Array.isArray(commander.partner_card_image_urls)
    ? commander.partner_card_image_urls
    : [];

  const partnerUris = Array.isArray(commander.partner_scryfall_uris)
    ? commander.partner_scryfall_uris
    : [];

  const cardNames = Array.isArray(commander.scryfall_card_names)
    ? commander.scryfall_card_names
    : [];

  const imageUrls = uniqueNonEmpty([
    ...partnerImages,
    commander.card_image_url,
    commander.scryfall_image_url,
    commander.image_url,
    commander.commander_image_url,
  ]);

  /*
    If no images exist yet, still return one placeholder entry so the layout
    stays card-shaped.
  */
  if (imageUrls.length === 0) {
    return [
      {
        imageUrl: PLACEHOLDER_IMAGE,
        label: commander.commander_name ?? "Commander card",
        scryfallUri: commander.scryfall_uri ?? null,
      },
    ];
  }

  /*
    Version 1 display:
    - one image for normal commanders
    - two images for paired commanders

    Even if more images somehow exist, cap at two so the layout remains clean.
  */
  return imageUrls.slice(0, 2).map((imageUrl, index) => ({
    imageUrl,
    label:
      cardNames[index] ??
      commander.commander_name ??
      `Commander card ${index + 1}`,
    scryfallUri:
      partnerUris[index] ??
      commander.scryfall_uri ??
      null,
  }));
}

function CardImage({ entry }) {
  return (
    <img
      src={entry.imageUrl}
      alt={entry.label}
      className="commander-image-gallery__image"
      loading="lazy"
      onError={(event) => {
        event.currentTarget.src = PLACEHOLDER_IMAGE;
      }}
    />
  );
}

function CardLinkOrButton({ entry, linked, linkTo, children, className }) {
  /*
    Search-card behavior:
      linked=true and linkTo exists
      -> clicking card image goes to the local commander detail page.

    Detail-page behavior:
      linked=false
      -> if Scryfall URI exists, clicking the image opens Scryfall.
      -> otherwise, use a non-link button-like wrapper for focus/hover support.
  */
  if (linked && linkTo) {
    return (
      <a className={className} href={linkTo} aria-label={`View details for ${entry.label}`}>
        {children}
      </a>
    );
  }

  if (entry.scryfallUri) {
    return (
      <a
        className={className}
        href={entry.scryfallUri}
        target="_blank"
        rel="noreferrer"
        aria-label={`View ${entry.label} on Scryfall`}
      >
        {children}
      </a>
    );
  }

  return (
    <button className={className} type="button" aria-label={entry.label}>
      {children}
    </button>
  );
}

export default function CommanderImageGallery({
  commander,
  size = "card",
  linked = false,
  linkTo = null,
}) {
  const imageEntries = getCommanderImageEntries(commander);
  const isPair = imageEntries.length > 1;

  const galleryClassName = [
    "commander-image-gallery",
    `commander-image-gallery--${size}`,
    isPair ? "commander-image-gallery--stacked" : "commander-image-gallery--single",
  ].join(" ");

  /*
    Single commander display:
    simple one-card display.
  */
  if (!isPair) {
    const entry = imageEntries[0];

    return (
      <div className={galleryClassName}>
        <figure className="commander-image-gallery__figure">
          <CardLinkOrButton
            entry={entry}
            linked={linked}
            linkTo={linkTo}
            className="commander-image-gallery__card commander-image-gallery__card--single"
          >
            <CardImage entry={entry} />
          </CardLinkOrButton>
        </figure>
      </div>
    );
  }

  /*
    Paired commander display:
    two-card stacked layout.

    Card 0:
      front card by default.

    Card 1:
      back card by default, shifted diagonally up/right.
      On hover/focus, CSS brings it forward.
  */
  return (
    <div className={galleryClassName}>
      <div
        className="commander-image-gallery__stack"
        aria-label={`${commander.commander_name} paired commander cards`}
      >
        {imageEntries.map((entry, index) => {
          const isBackCard = index === 1;

          return (
            <figure
              className={[
                "commander-image-gallery__figure",
                "commander-image-gallery__stack-item",
                isBackCard
                  ? "commander-image-gallery__stack-item--back"
                  : "commander-image-gallery__stack-item--front",
              ].join(" ")}
              key={`${entry.imageUrl}-${index}`}
            >
              <CardLinkOrButton
                entry={entry}
                linked={linked}
                linkTo={linkTo}
                className={[
                  "commander-image-gallery__card",
                  isBackCard
                    ? "commander-image-gallery__card--back"
                    : "commander-image-gallery__card--front",
                ].join(" ")}
              >
                <CardImage entry={entry} />
              </CardLinkOrButton>

              {size === "detail" ? (
                <figcaption className="commander-image-gallery__caption">
                  {entry.scryfallUri ? (
                    <a
                      href={entry.scryfallUri}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {entry.label}
                    </a>
                  ) : (
                    entry.label
                  )}
                </figcaption>
              ) : null}
            </figure>
          );
        })}
      </div>

      {size === "detail" ? (
        <p className="commander-image-gallery__hint">
          Hover or tab to the rear card to bring it forward.
        </p>
      ) : null}
    </div>
  );
}