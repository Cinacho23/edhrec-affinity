"""Train and validate the Pantlaza Synergy Deck."""

from . import pantlaza
from .commander_pipeline import freeze_target as _freeze_target, main


def freeze_target(latest, historical, output):
    return _freeze_target(latest, historical, output, commander_slug=pantlaza.COMMANDER_SLUG)


if __name__ == "__main__":
    raise SystemExit(main(default_commander=pantlaza.COMMANDER_SLUG))
