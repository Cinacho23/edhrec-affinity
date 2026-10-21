"""One public category per card; internal deckbuilding roles can still overlap."""


def normalize_deck_tags(deck: dict) -> None:
    commanders = {card["name"] for card in deck.get("commanders", [])}
    lines = []
    for card in deck.get("cards", []):
        role = ("Commander" if card["name"] in commanders else
                card.get("primary_role") or card.get("role") or next(iter(card.get("tags", [])), "Leftover"))
        role = str(role).replace("#", "").replace("\n", " ").strip() or "Leftover"
        card.update(tags=[role], primary_role=role)
        marker = " *CMDR*" if role == "Commander" else ""
        lines.append(f"{card['quantity']} {card['name']}{marker} #{role}")
    if lines:
        deck["export_text"] = "\n".join(lines) + "\n"


def normalize_report_deck_tags(report: dict) -> None:
    analysis = report.get("deck_analysis", {})
    packages = [*analysis.get("packages", []),
                *[package for model in analysis.get("alternative_models", []) for package in model.get("packages", [])]]
    for package in packages:
        if (deck := package.get("generated_deck", {})).get("status") == "generated":
            normalize_deck_tags(deck)
