"""Learn card fit from exact subtype and directed action matches."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..synergy_prototype.ranker import DeckRanker
from .typed_features import ACTIONS, FEATURE_COUNT, FEATURE_VERSION, catalog_traits


def _intersects(left, right):
    return np.any(left & right, axis=-1)


@dataclass(frozen=True)
class _CommandTraits:
    triggers: np.ndarray
    events: np.ndarray
    supports: np.ndarray
    focus: np.ndarray
    commander_types: np.ndarray


class TypedDeckRanker(DeckRanker):
    """Keep the masked-card objective and expose observable card interactions."""

    typed_feature_version = FEATURE_VERSION

    def __init__(self, cards, aliases, *, seed):
        super().__init__(cards, aliases, seed=seed)
        traits = catalog_traits(cards)
        rows = [traits[key] for key in self.keys]
        types = sorted({kind for row in rows for field in ("creature", "spell") if row.get(field) != "*"
                        for kind in row.get(field, ())})
        type_ids = {kind: index for index, kind in enumerate(types)}
        width = max(1, (len(types) + 7) // 8)
        all_types = (1 << len(types)) - 1

        def pack(kinds):
            bits = all_types if kinds == "*" else sum(1 << type_ids[kind] for kind in kinds if kind in type_ids)
            return np.frombuffer(bits.to_bytes(width, "little"), dtype=np.uint8)

        def masks(field):
            return np.stack([pack(row.get(field, ())) for row in rows])

        self._creature = masks("creature")
        self._spell = masks("spell")
        self._tokens = masks("tokens")
        self._supports = masks("supports")
        self._mentions = masks("mentions")
        self._triggers = np.stack([[pack(row.get("triggers", {}).get(action, ())) for action in ACTIONS] for row in rows])
        self._events = np.asarray([[action in row.get("events", ()) for action in ACTIONS] for row in rows])
        self._helpers = np.asarray([[action in row.get("helpers", ()) for action in ACTIONS] for row in rows])
        self._chooses = np.asarray([bool(row.get("chooses")) for row in rows])
        self._tribal = self._supports | np.bitwise_or.reduce(self._triggers, axis=1)
        self.hard_comparisons = 0
        self.same_plan_comparisons = 0
        self._frozen_base = None

    def initialize_from_browser_model(self, base):
        """Keep the established network and train its new interaction weights."""
        width = self.features.shape[1]
        widths = (5 * width, *self.model.hidden_layer_sizes, 1)
        shapes = [list(shape) for shape in zip(widths, widths[1:])]
        if base.get("shapes") != shapes or base.get("feature_count") != width or base.get("typed_feature_version"):
            raise ValueError("The starting model must use the matching legacy neural architecture")
        weights = [np.asarray(row, dtype=np.float32).reshape(shape)
                   for row, shape in zip(base["weights"], shapes, strict=True)]
        biases = [np.asarray(row, dtype=np.float32) for row in base["biases"]]
        if [bias.shape for bias in biases] != [(size,) for size in widths[1:]]:
            raise ValueError("The starting model has incomplete bias weights")

        self.model.set_params(batch_size=2)
        super()._fit_training_batch(np.zeros((2, widths[0] + FEATURE_COUNT), dtype=np.float32), np.asarray([0, 1]))
        self.model.coefs_[0][widths[0]:] = 0
        self._frozen_base = weights, biases
        self._restore_base_weights()

    def _restore_base_weights(self):
        weights, biases = self._frozen_base
        self.model.coefs_[0][:len(weights[0])] = weights[0]
        for array, saved in zip(self.model.coefs_[1:], weights[1:], strict=True):
            array[:] = saved
        for array, saved in zip(self.model.intercepts_, biases, strict=True):
            array[:] = saved

    def _fit_training_batch(self, features, labels):
        if self._frozen_base is not None:
            # Make one update before restoring the base for the next block.
            self.model.set_params(batch_size=len(labels))
        super()._fit_training_batch(features, labels)
        if self._frozen_base is not None:
            self._restore_base_weights()

    @lru_cache(maxsize=4096)
    def _plan(self, commanders):
        indices = list(commanders)
        triggers = np.bitwise_or.reduce(self._triggers[indices], axis=0)
        supports = np.bitwise_or.reduce(self._supports[indices], axis=0)
        events = np.any(triggers, axis=-1) | (np.any(supports) & np.any(self._events[indices], axis=0))
        focus = np.bitwise_or.reduce(triggers, axis=0) | supports
        return _CommandTraits(triggers, events, supports, focus, focus | np.bitwise_or.reduce(self._creature[indices], axis=0))

    def _relations(self, candidates, plans):
        targets = np.stack([plan.triggers for plan in plans])[:, None, :, :]
        body = _intersects(self._creature[candidates][..., None, :], targets)
        tokens = _intersects(self._tokens[candidates][..., None, :], targets)
        cast = ACTIONS.index("cast")
        body[..., cast] = _intersects(self._spell[candidates], targets[..., cast, :])
        tokens[..., cast] = False
        helpers = self._helpers[candidates] & np.stack([plan.events for plan in plans])[:, None, :]
        interactions = np.stack((body, tokens, helpers), axis=-1).reshape((*candidates.shape, 3 * len(ACTIONS)))
        recruited = _intersects(self._spell[candidates], np.stack([plan.supports for plan in plans])[:, None, :])
        support = _intersects(self._tribal[candidates], np.stack([plan.commander_types for plan in plans])[:, None, :])
        choose = self._chooses[candidates] & np.asarray([bool(np.any(plan.focus)) for plan in plans])[:, None]
        return np.concatenate((interactions, recruited[..., None], support[..., None], choose[..., None]), axis=-1)

    def _training_input(self, candidates, decks, commanders, context):
        relations = self._relations(candidates, [self._plan(deck.commanders) for deck in decks])
        return np.concatenate((self._input(candidates, commanders, context), relations), axis=-1)

    @lru_cache(maxsize=256)
    def _hard_alternatives(self, commanders, colors):
        plan = self._plan(commanders)
        if not np.any(plan.focus):
            return None
        eligible = np.flatnonzero(self.spells & ~self.holdout & ((self.masks & ~colors) == 0))
        relations = self._relations(eligible[None, :], [plan])[0]
        matches = np.any(relations, axis=1)
        # A legal card can mention the same subtype, or help another action,
        # without enabling the specific interaction seen in this deck.
        same_words = _intersects(self._mentions[eligible], plan.focus)
        other_action = np.any(self._helpers[eligible] & ~plan.events, axis=1)
        aligned = np.zeros(len(self.keys), dtype=bool)
        aligned[eligible] = matches
        by_cost = tuple(
            {
                cost: eligible[related & (self.cost_buckets[eligible] == cost)].tolist()
                for cost in range(5)
            }
            for related in (matches, same_words & ~matches, other_action & ~matches)
        )
        return aligned, by_cost

    def _comparable_negative(self, deck, positive, pool, excluded):
        hard = self._hard_alternatives(deck.commanders, deck.colors)
        if hard:
            aligned, (same_plan, same_words, other_action) = hard
            cost = self.cost_buckets[positive]
            # Use candidates that also fit the plan often enough that subtype
            # membership alone cannot solve the masked-card task.
            if not aligned[positive] or self.rng.random() < .75:
                alternatives = [index for index in same_plan[cost] if index not in excluded]
                if alternatives:
                    self.same_plan_comparisons += 1
                    return int(self.rng.choice(alternatives))
            if aligned[positive]:
                for by_cost in (same_words, other_action):
                    alternatives = [index for index in by_cost[cost] if index not in excluded]
                    if alternatives:
                        self.hard_comparisons += 1
                        return int(self.rng.choice(alternatives))
        return super()._comparable_negative(deck, positive, pool, excluded)

    def predict(self, candidates, command, context):
        candidates = np.asarray(candidates, dtype=np.int32)
        base = self._input(candidates, self._mean(command), self._mean(context))
        relation = self._relations(candidates[None, :], [self._plan(tuple(command))])[0]
        if relation.shape[-1] != FEATURE_COUNT:
            raise ValueError("Unexpected typed neural feature width")
        features = np.concatenate((base, relation), axis=-1)
        probabilities = np.clip(self.model.predict_proba(features)[:, 1], .0001, .9999)
        return np.log(probabilities / (1 - probabilities))
