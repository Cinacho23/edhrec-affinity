"""Learn to rank masked deck cards from text and a partial deck's features."""

from collections import Counter
from dataclasses import dataclass
import gzip
from hashlib import blake2b
import json
import re

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import normalize

from edhrec_affinity.build_styles.deckbuilding import _is_land
from edhrec_affinity.build_styles.sources import card_key
from edhrec_affinity.synergy_prototype.catalog import card_document, card_faces


COLORS = "WUBRG"
TYPES = ("creature", "instant", "sorcery", "artifact", "enchantment", "planeswalker", "battle", "land")
TEXT_DIMENSIONS = 40
BATCH_DECKS = 512


def color_mask(colors):
    return sum(1 << index for index, color in enumerate(COLORS) if color in colors)


def _numeric_stat(value):
    if value is None:
        return [0, 0, 0]
    try:
        return [min(20, max(-5, float(value))) / 8, 1, 0]
    except ValueError:
        return [0, 1, 1]


def _face_stats(face):
    cost = face.get("mana_cost", "")
    pips = re.findall(r"\{([^}]+)\}", cost)
    return [
        *[float(color in face.get("colors", ())) for color in COLORS],
        *[sum(color in pip.split("/") for pip in pips) / 3 for color in COLORS],
        float(bool(cost)), float("X" in pips), float(any("/" in pip for pip in pips)),
        *_numeric_stat(face.get("power")), *_numeric_stat(face.get("toughness")),
    ]


def _stats(card):
    faces = card_faces(card)
    types = card["type_line"].lower().split("—", 1)[0].split()
    extra_faces = [_face_stats(face) for face in faces[1:]] or [_face_stats({})]
    return [
        min(20, card["cmc"]) / 8,
        *[float(color in card["color_identity"]) for color in COLORS],
        *[float(kind in types) for kind in TYPES],
        *_face_stats(faces[0]), *np.max(extra_faces, axis=0),
    ]


@dataclass(frozen=True, slots=True)
class DeckExample:
    commanders: tuple[int, ...]
    spells: tuple[int, ...]
    held_out: tuple[int, ...]
    colors: int


class DeckRanker:
    def __init__(self, cards, aliases, *, seed):
        self.keys = list(cards)
        self.index = {key: index for index, key in enumerate(self.keys)}
        self.alias_indices = {alias: self.index[key] for alias, key in aliases.items()}
        self.masks = np.asarray([color_mask(card["color_identity"]) for card in cards.values()])
        self.spells = np.asarray([not _is_land(card) for card in cards.values()])
        self.holdout = np.asarray([
            int(card["oracle_id"].split("-")[-1], 16) % 13 == 0 for card in cards.values()
        ]) & self.spells
        self.mana_values = np.asarray([card["cmc"] for card in cards.values()])
        self.cost_buckets = np.digitize(self.mana_values, [2, 3, 4, 6])
        documents = [card_document(card) for card in cards.values()]
        text = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=18000,
                               dtype=np.float32).fit_transform(documents)
        dense_text = TruncatedSVD(n_components=TEXT_DIMENSIONS, random_state=seed).fit_transform(text)
        self.text = normalize(dense_text).astype(np.float32, copy=False)
        stats = np.asarray([_stats(card) for card in cards.values()], dtype=np.float32)
        self.features = np.column_stack([self.text, stats]).astype(np.float32, copy=False)
        self.empty = np.zeros(self.features.shape[1], dtype=np.float32)
        self.rng = np.random.default_rng(seed)
        self.model = MLPClassifier(hidden_layer_sizes=(96, 48, 24), batch_size=256,
                                   learning_rate_init=0.0015, alpha=0.001, random_state=seed)

    def load_decks(self, directory, commander_keys):
        target_ids = tuple(sorted(self.index[key] for key in commander_keys))
        training, testing = [], []
        seen = set()
        stats = Counter()
        name_cache = {}

        def resolve(name):
            if name not in name_cache:
                name_cache[name] = self.alias_indices.get(card_key(name))
            return name_cache[name]

        for path in sorted(directory.glob("*.json.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                checkpoint = json.load(handle)
            stats["checkpoint_files"] += 1
            for result in checkpoint["results"].values():
                deck = result.get("deck")
                if not deck:
                    continue
                stats["parsed_decks"] += 1
                command = [resolve(name) for name in deck["commanders"]]
                if not command or None in command or len(set(command)) != len(command):
                    continue
                commanders = tuple(sorted(command))
                if commanders == target_ids:
                    stats["target_pair_decks"] += 1
                # Exclude interactions in which a held-out card appeared in the command zone too.
                if any(self.holdout[index] for index in commanders):
                    continue
                colors = int(np.bitwise_or.reduce(self.masks[list(commanders)]))
                counts = Counter()
                for row in deck["cards"]:
                    index = resolve(row["name"])
                    if index is not None and index not in commanders and not self.masks[index] & ~colors:
                        counts[index] += row["quantity"]
                if not 90 <= sum(counts.values()) + len(commanders) <= 100:
                    continue
                order = sorted(counts)
                active = tuple(index for index in order if self.spells[index] and not self.holdout[index])
                if len(active) < 25:
                    continue
                fingerprint = np.asarray((*commanders, -1, *((index << 8) | counts[index] for index in order)),
                                         dtype="<i4")
                key = blake2b(fingerprint.tobytes(), digest_size=12).digest()
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                if key[0] < 32 or commanders == target_ids:
                    held_out = tuple(index for index in order if self.holdout[index])
                    if held_out:
                        testing.append(DeckExample(commanders, active, held_out, colors))
                else:
                    training.append(DeckExample(commanders, active, (), colors))
        if not training or not testing:
            raise ValueError("The saved checkpoint needs both training data and held-out card examples")
        return training, testing, dict(stats)

    def _input(self, candidates, commanders, context):
        features = self.features[candidates]
        # A bounded cross-feature term lets the model learn trigger/payoff pairings
        # as well as evidence from the full feature vector of each side.
        return np.concatenate([
            features, np.broadcast_to(commanders, features.shape), np.broadcast_to(context, features.shape),
            features * commanders, features * context,
        ], axis=-1)

    def _mean(self, indices):
        return np.mean(self.features[list(indices)], axis=0) if len(indices) else self.empty

    def _take_negative(self, pool, excluded):
        while True:
            candidate = int(pool[self.rng.integers(len(pool))])
            if candidate not in excluded:
                return candidate

    def fit(self, decks, *, epochs):
        popularity = np.zeros(len(self.keys), dtype=np.int32)
        for deck in decks:
            np.add.at(popularity, list(deck.spells), 1)
        eligible = self.spells & ~self.holdout
        legal = {mask: np.flatnonzero(eligible & ((self.masks & ~mask) == 0)) for mask in range(32)}
        popular = {}
        for mask, pool in legal.items():
            weights = np.sqrt(popularity[pool] + 0.2)
            popular[mask] = self.rng.choice(pool, size=65536, p=weights / weights.sum())
        same_cost = {(mask, cost): pool[self.cost_buckets[pool] == cost]
                     for mask, pool in legal.items() for cost in range(5)}
        commander_features = {keys: self._mean(keys) for keys in {deck.commanders for deck in decks}}
        for epoch in range(epochs):
            shuffled = self.rng.permutation(len(decks))
            for start in range(0, len(decks), BATCH_DECKS):
                block = [decks[int(index)] for index in shuffled[start:start + BATCH_DECKS]]
                candidate_ids = np.empty((len(block), 4), dtype=np.int32)
                commander_matrix = np.empty((len(block), 1, len(self.empty)), dtype=np.float32)
                context_matrix = np.empty_like(commander_matrix)
                for index, deck in enumerate(block):
                    order = self.rng.permutation(deck.spells)
                    positive = int(order[0])
                    excluded = set(deck.spells) | set(deck.commanders)
                    uniform = self._take_negative(legal[deck.colors], excluded)
                    excluded.add(uniform)
                    common = self._take_negative(popular[deck.colors], excluded)
                    excluded.add(common)
                    cost_pool = same_cost[deck.colors, self.cost_buckets[positive]]
                    similar = self._take_negative(cost_pool if len(cost_pool) > len(excluded) else legal[deck.colors],
                                                  excluded)
                    candidate_ids[index] = [positive, uniform, common, similar]
                    commander_matrix[index, 0] = commander_features[deck.commanders]
                    # Randomly vary the information available at inference time.
                    context_size = int(self.rng.integers(0, len(order)))
                    context_matrix[index, 0] = self._mean(order[1:1 + context_size])
                features = self._input(candidate_ids, commander_matrix, context_matrix)
                labels = np.tile([1, 0, 0, 0], len(block))
                self.model.partial_fit(features.reshape(len(labels), -1), labels, classes=np.asarray([0, 1]))
            print(f"Finished training epoch {epoch + 1}/{epochs}", flush=True)

    def predict(self, candidates, command, context):
        features = self._input(candidates, self._mean(command), self._mean(context))
        probabilities = np.clip(self.model.predict_proba(features)[:, 1], 0.0001, 0.9999)
        return np.log(probabilities / (1 - probabilities))

    def for_commanders(self, commander_keys):
        command = tuple(self.index[key] for key in commander_keys)
        colors = int(np.bitwise_or.reduce(self.masks[list(command)]))
        candidates = np.flatnonzero(self.spells & ((self.masks & ~colors) == 0))
        candidates = candidates[~np.isin(candidates, command)]
        keys = [self.keys[index] for index in candidates]

        def rank(partial):
            context = tuple(self.index[key] for key in partial if self.spells[self.index[key]])
            scores = self.predict(candidates, command, context)
            return dict(zip(keys, scores.tolist()))

        return rank

    def evaluate(self, examples, *, limit=1000, candidates_per_query=100):
        """Hold out identities globally, then rank a known inclusion among legal held-out peers."""
        legal = {mask: np.flatnonzero(self.holdout & ((self.masks & ~mask) == 0)) for mask in range(32)}
        ranks = np.zeros((3, min(limit, len(examples))), dtype=np.float32)
        for i, index in enumerate(self.rng.permutation(len(examples))[:limit]):
            example = examples[int(index)]
            positive = int(self.rng.choice(example.held_out))
            pool = legal[example.colors]
            pool = pool[~np.isin(pool, example.held_out)]
            sampled = self.rng.choice(pool, size=min(candidates_per_query - 1, len(pool)), replace=False)
            candidates = np.append(sampled, positive)
            context = self.rng.permutation(example.spells)[:16]
            similarity_query = (self._mean(example.commanders)[:TEXT_DIMENSIONS]
                                + self._mean(context)[:TEXT_DIMENSIONS])
            score_rows = [self.predict(candidates, example.commanders, context),
                          self.predict(candidates, example.commanders, ()),
                          self.text[candidates] @ similarity_query]
            for row, scores in enumerate(score_rows):
                # Split ties rather than giving the known positive an ordering advantage.
                ranks[row, i] = (1 + np.count_nonzero(scores[:-1] > scores[-1])
                                 + np.count_nonzero(scores[:-1] == scores[-1]) / 2)
        return {"queries": ranks.shape[1], "max_candidates_per_query": candidates_per_query,
                "held_out_identities": int(np.count_nonzero(self.holdout)),
                **{name: {"recall_at_10": round(float(np.mean(row <= 10)), 4),
                          "mean_reciprocal_rank": round(float(np.mean(1 / row)), 4)}
                   for name, row in zip(("neural_context", "neural_commander_only", "text_similarity"), ranks)}}
