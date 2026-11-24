"""Learn candidate-specific relationships to the individual cards in a partial deck."""

from copy import copy

import numpy as np
from scipy.special import expit
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import normalize

from .ranker import BATCH_DECKS, DeckRanker


class InteractionDeckRanker(DeckRanker):
    """Train a pairwise attention layer, then learn masked-card scores from its pooled context."""

    @classmethod
    def from_ranker(cls, baseline, *, seed):
        """Share the baseline's immutable catalog features and rebuild its learned layers."""
        ranker = cls.__new__(cls)
        ranker.__dict__.update(baseline.__dict__)
        ranker.rng = np.random.default_rng(seed)
        ranker.model = MLPClassifier(**{**baseline.model.get_params(), "random_state": seed})
        return ranker

    def _training_batches(self, decks):
        popularity = np.zeros(len(self.keys), dtype=np.int32)
        for deck in decks:
            np.add.at(popularity, list(deck.spells), 1)
        eligible = self.spells & ~self.holdout
        legal = {mask: np.flatnonzero(eligible & ((self.masks & ~mask) == 0)) for mask in range(32)}
        popular = {}
        similar = {}
        for mask, pool in legal.items():
            weights = np.sqrt(popularity[pool] + .2)
            popular[mask] = self.rng.choice(pool, size=65536, p=weights / weights.sum())
            for cost in range(5):
                similar[mask, cost] = pool[self.cost_buckets[pool] == cost]
        command = {keys: self._mean(keys) for keys in {deck.commanders for deck in decks}}
        shuffled = self.rng.permutation(len(decks))
        for start in range(0, len(decks), BATCH_DECKS):
            block = [decks[int(index)] for index in shuffled[start:start + BATCH_DECKS]]
            candidates = np.empty((len(block), 4), dtype=np.int32)
            commanders = np.empty((len(block), 1, len(self.empty)), dtype=np.float32)
            context = np.full((len(block), max(len(deck.spells) for deck in block) - 1), -1, dtype=np.int32)
            for index, deck in enumerate(block):
                order = self.rng.permutation(deck.spells)
                positive = int(order[0])
                excluded = set(deck.spells) | set(deck.commanders)
                uniform = self._take_negative(legal[deck.colors], excluded)
                excluded.add(uniform)
                common = self._take_negative(popular[deck.colors], excluded)
                excluded.add(common)
                same_cost = similar[deck.colors, self.cost_buckets[positive]]
                comparable = self._take_negative(same_cost if len(same_cost) > len(excluded) else legal[deck.colors],
                                                 excluded)
                candidates[index] = [positive, uniform, common, comparable]
                commanders[index, 0] = command[deck.commanders]
                count = int(self.rng.integers(0, len(order)))
                context[index, :count] = order[1:1 + count]
            yield candidates, commanders, context

    def _learn_pair_weights(self, decks):
        # Standardize on training identities only. Text and card stats start on
        # different scales, especially for binary type/color flags.
        training_features = self.features[self.spells & ~self.holdout]
        scale = np.maximum(training_features.std(axis=0), .15)
        self.pair_features = normalize((self.features - training_features.mean(axis=0)) / scale)
        weights = np.zeros((len(self.empty), len(self.empty)), dtype=np.float32)
        first_moment = np.zeros_like(weights)
        second_moment = np.zeros_like(weights)
        for step, (candidates, _, context) in enumerate(self._training_batches(decks), start=1):
            active = context[:, 0] >= 0
            if not np.any(active):
                continue
            # Learn which candidate completes the same observed card pairing.
            # Cycle three types of absent-but-legal alternatives between batches.
            anchors = self.pair_features[context[active, 0]]
            present, alternate = candidates[active, 0], candidates[active, 1 + (step - 1) % 3]
            difference = self.pair_features[present] - self.pair_features[alternate]
            margins = np.sum((difference @ weights) * anchors, axis=1)
            gradient = difference.T @ ((expit(margins) - 1)[:, None] * anchors) / len(anchors) + 1e-5 * weights
            first_moment = .9 * first_moment + .1 * gradient
            second_moment = .999 * second_moment + .001 * gradient**2
            weights -= .025 * (first_moment / (1 - .9**step)) / (np.sqrt(second_moment / (1 - .999**step)) + 1e-8)
        self.pair_weights = weights
        self.projected_pairs = self.pair_features @ self.pair_weights

    def _attended_context(self, candidates, context):
        if not context.shape[1]:
            return np.zeros((*candidates.shape, len(self.empty)), dtype=np.float32)
        present = context >= 0
        indices = np.maximum(context, 0)
        keys = self.pair_features[indices]
        logits = np.clip(self.projected_pairs[candidates] @ keys.swapaxes(-1, -2), -4, 4)
        weights = np.exp(logits) * present[:, None, :]
        weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-12)
        return weights @ self.features[indices]

    def _interaction_input(self, candidates, commanders, context):
        feature_context = self._attended_context(candidates, context)
        return self._input(candidates, commanders, feature_context)

    def fit(self, decks, *, epochs):
        self._learn_pair_weights(decks)
        print("Finished learning candidate-to-card relationships", flush=True)
        for epoch in range(epochs):
            for candidates, commanders, context in self._training_batches(decks):
                features = self._interaction_input(candidates, commanders, context)
                labels = np.tile([1, 0, 0, 0], len(candidates))
                self.model.partial_fit(features.reshape(len(labels), -1), labels, classes=np.asarray([0, 1]))
            print(f"Finished interaction training epoch {epoch + 1}/{epochs}", flush=True)

    def predict(self, candidates, command, context):
        shape = (1, 1, len(self.empty))
        features = self._interaction_input(np.asarray(candidates)[None, :], self._mean(command).reshape(shape),
                                           np.asarray(context, dtype=np.int32)[None, :])[0]
        probabilities = np.clip(self.model.predict_proba(features)[:, 1], .0001, .9999)
        return np.log(probabilities / (1 - probabilities))


def paired_evaluation(baseline, interaction, testing, *, seed):
    """Compare both trained models on exactly the same decks and sampled alternatives."""
    evaluations = []
    for ranker in (baseline, interaction):
        replay = copy(ranker)
        replay.rng = np.random.default_rng(seed)
        evaluations.append(replay.evaluate(testing))
    return {"seed": seed, "baseline": evaluations[0], "interaction": evaluations[1]}
