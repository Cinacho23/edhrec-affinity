import numpy as np
from sklearn.neural_network import MLPClassifier

from edhrec_affinity.synergy_decks.typed_features import ACTIONS, FEATURE_COUNT, catalog_traits
from edhrec_affinity.synergy_decks.typed_ranker import TypedDeckRanker
from edhrec_affinity.synergy_prototype.ranker import DeckRanker


def card(name, type_line, text=""):
    return {"name": name, "type_line": type_line, "oracle_text": text, "card_faces": []}


def test_exact_subtypes_are_tied_to_the_trigger_subject():
    cards = {
        "enter": card("Chief, the Finder", "Legendary Creature — Dinosaur",
                      "Whenever Chief, the Finder or another Dinosaur enters under your control, draw a card."),
        "attack": card("General", "Legendary Creature — Dinosaur", "Whenever a Dinosaur attacks, draw a card."),
        "opponent": card("Hunter", "Legendary Creature — Human", "Whenever an opponent casts a Dinosaur spell, draw a card."),
        "output": card("Keeper", "Legendary Creature — Human", "Whenever another Human dies, create a 3/3 Dinosaur creature token."),
    }
    traits = catalog_traits(cards)

    assert traits["enter"]["triggers"] == {"enter": ["Dinosaur"]}
    assert traits["attack"]["triggers"] == {"attack": ["Dinosaur"]}
    assert "triggers" not in traits["opponent"]
    assert traits["output"]["triggers"] == {"die": ["Human"]}
    assert traits["output"]["tokens"] == ["Dinosaur"]


def test_relations_distinguish_tokens_creature_casts_and_unrelated_helpers(monkeypatch):
    cards = {
        "enter": card("Chief", "Legendary Creature — Dinosaur", "Whenever a Dinosaur enters, draw a card."),
        "cast": card("Sage", "Legendary Creature — Human", "Whenever you cast a Dinosaur spell, draw a card."),
        "token": card("Hatchery", "Sorcery", "Create a 3/3 green Dinosaur creature token."),
        "blink": card("Return", "Instant", "Exile target creature you control, then return it to the battlefield."),
        "haste": card("March", "Instant", "Target creature gains haste until end of turn."),
        "body": card("Ancient", "Creature — Dinosaur"),
        "support": card("Tamer", "Creature — Human", "Dinosaur spells you cast cost {1} less to cast."),
        "hunt": card("Hunt", "Instant", "Destroy target Dinosaur."),
    }
    monkeypatch.setattr(DeckRanker, "__init__", lambda self, cards, *args, **kwargs: setattr(self, "keys", list(cards)))
    ranker = TypedDeckRanker(cards, {}, seed=42)
    candidates = np.asarray([[2, 3, 4, 5, 6, 7]] * 2)
    relations = ranker._relations(candidates, [ranker._plan((0,)), ranker._plan((1,))])
    enter, cast = (3 * ACTIONS.index(action) for action in ("enter", "cast"))

    assert relations.shape == (2, 6, FEATURE_COUNT)
    assert relations[0, 0, enter + 1] and not relations[1, 0].any()
    assert relations[0, 1, enter + 2] and not relations[0, 2].any()
    assert relations[0, 3, enter] and relations[1, 3, cast]
    assert relations[:, 4, -2].all()
    assert not relations[:, 5].any()


def test_transfer_training_updates_typed_weights_and_preserves_the_base():
    ranker = TypedDeckRanker.__new__(TypedDeckRanker)
    ranker.features = np.zeros((1, 1), dtype=np.float32)
    ranker.model = MLPClassifier(hidden_layer_sizes=(2,), random_state=1)
    base = {"feature_count": 1, "shapes": [[5, 2], [2, 1]],
            "weights": [[.25] * 10, [.5, .5]], "biases": [[.25, .25], [0]]}
    ranker.initialize_from_browser_model(base)
    features = np.zeros((4, 5 + FEATURE_COUNT), dtype=np.float32)
    features[0, 5] = 1
    ranker._fit_training_batch(features, np.asarray([1, 0, 0, 0]))

    np.testing.assert_array_equal(ranker.model.coefs_[0][:5].ravel(), base["weights"][0])
    np.testing.assert_array_equal(ranker.model.coefs_[1].ravel(), base["weights"][1])
    for observed, expected in zip(ranker.model.intercepts_, base["biases"], strict=True):
        np.testing.assert_array_equal(observed, expected)
    assert np.any(ranker.model.coefs_[0][5:])
