"""Two experiments: enriched card-profile similarity and unlabeled deck NMF.

All scores here are exploratory similarities or relative component loadings.
They are unrelated to the project's specialization z-scores.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
import math
from urllib.parse import urlsplit
import warnings

import numpy as np
from scipy import sparse

from .sources import card_key


def cosine(left, right) -> float:
    left, right = np.asarray(left), np.asarray(right)
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    return float(np.clip(left @ right / denominator, 0, 1)) if denominator > 0 else 0.0


def enriched_vectors(base: dict, profiles: list[dict], prior_decks: float = 20) -> dict:
    """Regularize toward this commander's baseline, then keep positive excess.

    The 20-deck regularizer is an explicit pilot heuristic, not a fitted prior.
    An absent card is never converted into an observed zero-inclusion rate.
    """
    result = {}
    for profile in profiles:
        vector = {}
        for key in base["cards"].keys() & profile["cards"].keys():
            card = profile["cards"][key]
            baseline = base["cards"][key]["rate"]
            adjusted = (card["count"] + prior_decks * baseline) / (card["denominator"] + prior_decks)
            excess = max(0.0, adjusted - baseline)
            vector[key] = excess if excess >= 0.01 else 0.0
        result[profile["tag_slug"]] = vector
    return result


def overlapping_groups(slugs: list[str], pairs: list[dict], threshold: float) -> list[list[str]]:
    """Maximal cliques allow one tag to belong to several candidate groups."""
    neighbors = {slug: set() for slug in slugs}
    for pair in pairs:
        if pair["similarity"] is not None and pair["similarity"] >= threshold:
            neighbors[pair["left"]].add(pair["right"])
            neighbors[pair["right"]].add(pair["left"])
    groups = []

    def visit(selected: set, candidates: set, excluded: set):
        if not candidates and not excluded:
            groups.append(sorted(selected))
            return
        pivot = max(candidates | excluded, key=lambda item: len(neighbors[item]), default=None)
        for item in sorted(candidates - (neighbors[pivot] if pivot else set())):
            visit(selected | {item}, candidates & neighbors[item], excluded & neighbors[item])
            candidates.remove(item)
            excluded.add(item)

    visit(set(), set(slugs), set())
    return sorted(groups, key=lambda group: (-len(group), group))


def compare_profiles(base: dict, profiles: list[dict], *, threshold: float = 0.6,
                     min_shared: int = 20) -> dict:
    vectors = enriched_vectors(base, profiles)
    summaries = []
    for profile in profiles:
        vector = vectors[profile["tag_slug"]]
        top = sorted(vector, key=lambda key: (-vector[key], key))
        summaries.append({
            "tag_slug": profile["tag_slug"], "tag_name": profile["tag_name"],
            "deck_count": profile["deck_count"], "observed_cards": len(profile["cards"]),
            "comparable_cards": len(vector), "ignored": profile["ignored"],
            "signature_cards": [{"name": profile["cards"][key]["name"], "excess": vector[key],
                                 "tag_rate": profile["cards"][key]["rate"],
                                 "baseline_rate": base["cards"][key]["rate"]}
                                for key in top[:12] if vector[key] > 0],
        })
    pairs = []
    for left, right in combinations(profiles, 2):
        a, b = vectors[left["tag_slug"]], vectors[right["tag_slug"]]
        shared = sorted(a.keys() & b.keys())
        sufficient = (len(shared) >= min_shared and
                      sum(a[key] > 0 for key in shared) >= 3 and
                      sum(b[key] > 0 for key in shared) >= 3)
        strongest = sorted(shared, key=lambda key: (-a[key] * b[key], key))
        pairs.append({
            "left": left["tag_slug"], "right": right["tag_slug"],
            "similarity": cosine([a[k] for k in shared], [b[k] for k in shared]) if sufficient else None,
            "raw_similarity": cosine([left["cards"][k]["rate"] for k in shared],
                                     [right["cards"][k]["rate"] for k in shared]) if shared else None,
            "shared_observed_cards": len(shared),
            "reason": None if sufficient else "Insufficient shared measurements or enriched cards",
            "shared_signature_cards": [{"name": base["cards"][key]["name"],
                                        "left_excess": a[key], "right_excess": b[key]}
                                       for key in strongest[:8] if a[key] * b[key] > 0],
        })
    return {
        "method": "Cosine similarity of positive card enrichment above the same commander baseline",
        "threshold": threshold, "regularizer_decks": 20, "minimum_excess": 0.01,
        "minimum_shared_cards": min_shared, "profiles": summaries, "pairs": pairs,
        "groups": overlapping_groups([p["tag_slug"] for p in profiles], pairs, threshold),
        "interpretation": "Candidate overlapping tag groups; no deck intersections or build shares inferred.",
    }


def prepare_decks(decks: list[dict], commander_names: list[str], *, min_cards: int = 90,
                  commander_name_groups: list[list[str]] | None = None) -> tuple:
    expected = {card_key(name) for name in commander_names}
    allowed = {frozenset(expected)}
    for group in commander_name_groups or []:
        if (isinstance(group, list) and len(group) == len(commander_names)
                and all(isinstance(name, str) and name.strip() for name in group)):
            keys = frozenset(card_key(name) for name in group)
            if len(keys) == len(expected):
                allowed.add(keys)
    commander_keys = set().union(*allowed)
    kept, rejected, seen = [], [], set()
    duplicates = 0
    for deck in decks:
        actual = deck.get("commanders", [])
        if (len(actual) != len(commander_names)
                or frozenset(card_key(name) for name in actual) not in allowed):
            rejected.append({"id": deck["id"], "reason": "Commander mismatch"})
            continue
        counts = Counter()
        types, names = {}, {}
        for card in deck["cards"]:
            key = card_key(card["name"])
            if key not in commander_keys:
                counts[key] += card["quantity"]
                names[key] = card["name"]
                types[key] = card.get("type_line", "")
        card_count = sum(counts.values()) + len(expected)
        if not min_cards <= card_count <= 100:
            rejected.append({"id": deck["id"], "reason": f"{card_count} cards; expected {min_cards}–100"})
            continue
        fingerprint = tuple(sorted(counts.items()))
        if fingerprint in seen:
            duplicates += 1
            continue
        seen.add(fingerprint)
        kept.append({**deck, "counts": counts, "names": names, "types": types, "card_count": card_count})
    return kept, rejected, duplicates


def _fit_nmf(matrix, components: int, seed: int, init: str = "random"):
    try:
        from sklearn.decomposition import NMF
        from sklearn.exceptions import ConvergenceWarning
    except ImportError as error:
        raise RuntimeError('Install the prototype extra: pip install -e ".[prototype]"') from error
    model = NMF(n_components=components, init=init, random_state=seed, max_iter=1500, tol=1e-5)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ConvergenceWarning)
        weights = model.fit_transform(matrix)
    return model, weights, not any(issubclass(w.category, ConvergenceWarning) for w in captured)


def _aligned_similarity(reference, candidate) -> float:
    from scipy.optimize import linear_sum_assignment

    similarities = np.array([[cosine(a, b) for b in candidate] for a in reference])
    rows, columns = linear_sum_assignment(-similarities)
    return float(similarities[rows, columns].mean())


def _memberships(model, weights):
    """Remove the arbitrary NMF component scale before comparing deck weights."""
    scaled = weights * np.linalg.norm(model.components_, axis=1)
    return scaled / np.maximum(scaled.sum(axis=1, keepdims=True), 1e-12)


def _evaluate_components(matrix, components: int, seed: int, membership_threshold: float) -> dict:
    """Use held-out decks and repeated fits; source labels never enter this step.

    Feature vocabulary and IDF weights are fixed for comparisons between K values.
    Validation therefore measures reconstruction in this fixed feature space, not
    prediction of unseen cards or an independent population validation.
    """
    fits = [_fit_nmf(matrix, components, seed, "nndsvda")]
    fits.extend(_fit_nmf(matrix, components, seed + offset) for offset in range(3))
    model, weights, converged = min(fits, key=lambda fit: (not fit[2], fit[0].reconstruction_err_))
    memberships = _memberships(model, weights)
    stability = [_aligned_similarity(model.components_, other.components_)
                 for other, _, _ in fits if other is not model]
    rng = np.random.default_rng(seed)
    subsample, validation, validation_converged = [], [], []
    for offset in range(3):
        indexes = rng.choice(matrix.shape[0], max(components + 1, math.floor(0.8 * matrix.shape[0])), replace=False)
        held_out = np.ones(matrix.shape[0], dtype=bool)
        held_out[indexes] = False
        candidate, _, fit_converged = _fit_nmf(matrix[indexes], components, seed + 100 + offset)
        # NMF.transform changes only held-out deck weights, keeping card packages fixed.
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            held_weights = candidate.transform(matrix[held_out])
        validation.append(_reconstruction_ratio(matrix[held_out], held_weights, candidate.components_))
        validation_converged.append(fit_converged and not captured)
        subsample.append(_aligned_similarity(model.components_, candidate.components_))
    minimum_count = int((memberships >= membership_threshold).sum(axis=0).min())
    minimum_effective = float(memberships.sum(axis=0).min())
    minimum_required = max(3, math.ceil(0.05 * matrix.shape[0]))
    seed_consistency = float(np.mean(stability))
    subsample_consistency = float(np.mean(subsample))
    supported = minimum_count >= minimum_required and minimum_effective >= minimum_required
    stable = seed_consistency >= 0.9 and subsample_consistency >= 0.8
    fit_ok = converged and all(validation_converged)
    exclusions = []
    if not supported:
        exclusions.append(f"Each package needs at least {minimum_required} member and effective decks")
    if not stable:
        exclusions.append("Package directions did not recur consistently across seeds and held-out fits")
    if not fit_ok:
        exclusions.append("At least one model fit did not converge")
    error = float(np.mean(validation))
    # Explicit conservative heuristic: one deck-equivalent per extra component.
    # This is not AIC, a calibrated probability, or proof of an optimal K.
    penalty = (components - 1) / matrix.shape[0]
    diagnostic = {
        "components": components, "validation_error": error,
        "validation_error_std": float(np.std(validation, ddof=1)),
        "validation_error_se": float(np.std(validation, ddof=1) / math.sqrt(len(validation))),
        "complexity_penalty": penalty, "score": error + penalty,
        "minimum_member_count": minimum_count, "minimum_effective_members": minimum_effective,
        "minimum_required_members": minimum_required, "supported": supported,
        "seed_consistency": seed_consistency, "subsample_consistency": subsample_consistency,
        "converged": fit_ok, "eligible": supported and stable and fit_ok,
        "reason": "; ".join(exclusions) if exclusions else "Supported and consistent in this sample",
        "selected": False,
    }
    return {"model": model, "weights": weights, "memberships": memberships,
            "converged": converged, "diagnostic": diagnostic}


def _reconstruction_ratio(matrix, weights, components) -> float:
    if not sparse.issparse(matrix):
        return float(np.square(matrix - weights @ components).sum() / max(np.square(matrix).sum(), 1e-12))
    # ||X-WH||² without materializing a dense decks × cards prediction.
    norm = float(matrix.multiply(matrix).sum())
    prediction_norm = float(np.sum((weights.T @ weights) * (components @ components.T)))
    cross = float(np.sum((matrix @ components.T) * weights))
    return max(0.0, norm + prediction_norm - 2 * cross) / max(norm, 1e-12)


def _package_summary(fit: dict, kept: list[dict], vocabulary: list[str], presence,
                     idf, vectors: dict, profiles: list[dict], membership_threshold: float) -> dict:
    model, memberships = fit["model"], fit["memberships"]
    names = {key: name for deck in kept for key, name in deck["names"].items()}
    package_rows = []
    for index, component in enumerate(model.components_):
        members = memberships[:, index] >= membership_threshold
        annotations = []
        for profile in profiles:
            vector = vectors[profile["tag_slug"]]
            common = [i for i, key in enumerate(vocabulary) if key in vector]
            if len(common) < 20 or sum(vector[vocabulary[i]] > 0 for i in common) < 3:
                continue
            annotations.append({"tag_name": profile["tag_name"], "tag_slug": profile["tag_slug"],
                                "similarity": cosine(component[common], [vector[vocabulary[i]] * idf[i] for i in common]),
                                "compared_cards": len(common)})
        annotations.sort(key=lambda row: (-row["similarity"], row["tag_slug"]))
        suggestion_floor = max(0.2, 0.5 * annotations[0]["similarity"]) if annotations else 0.2
        strongest = np.argsort(-component, kind="stable")[:12]
        cards = []
        for column in strongest:
            within = float(presence[members, column].mean()) if members.any() else None
            outside = float(presence[~members, column].mean()) if (~members).any() else None
            cards.append({"name": names[vocabulary[column]], "relative_weight": float(component[column] / max(component.max(), 1e-12)),
                          "member_rate": within, "other_rate": outside,
                          "difference": within - outside if within is not None and outside is not None else None})
        representative_indexes = sorted(range(len(kept)), key=lambda i: -memberships[i, index])[:3]
        package_rows.append({
            "id": index, "name": f"Package {index + 1}",
            "tag_suggestions": [row for row in annotations if row["similarity"] >= suggestion_floor],
            "tag_suggestion_minimum_similarity": suggestion_floor,
            "member_count": int(members.sum()), "top_cards": cards,
            "representative_deck_ids": [kept[i]["id"] for i in representative_indexes],
        })
    components = len(model.components_)
    overlap = [
        {"left": a, "right": b, "deck_count": int(((memberships[:, a] >= membership_threshold) &
                                                    (memberships[:, b] >= membership_threshold)).sum())}
        for a, b in combinations(range(components), 2)
    ]
    norm = math.sqrt(len(kept))  # Deck vectors have unit L2 norm.
    error = float(model.reconstruction_err_ / norm)
    return {
        "components": components, "packages": package_rows, "overlap": overlap,
        "converged": fit["converged"], "relative_training_error": error,
        "weighted_reconstruction_captured": float(1 - error ** 2),
        "seed_consistency": fit["diagnostic"]["seed_consistency"],
        "subsample_consistency": fit["diagnostic"]["subsample_consistency"],
        "memberships": [{"id": deck["id"], "weights": memberships[i].tolist()}
                        for i, deck in enumerate(kept)],
    }


def analyze_decks(decks: list[dict], base: dict, profiles: list[dict], *, components: int | None = None,
                  max_components: int = 8, seed: int = 42, min_cards: int = 90, min_support: int = 2,
                  membership_threshold: float = 0.25) -> dict:
    """Learn flexible card packages, never source tags; weights are not probabilities.

    ``components=None`` chooses a conservative count from 1 through a data-supported
    maximum. An explicit positive count requests that many components for exploration.
    Automatic selection is a diagnostic heuristic, not a discovered ground truth.
    """
    if components is not None and (isinstance(components, bool) or not isinstance(components, int) or components < 1):
        raise ValueError("components must be a positive integer or None for automatic selection")
    if isinstance(max_components, bool) or not isinstance(max_components, int) or max_components < 1:
        raise ValueError("max_components must be a positive integer")
    if not 0 < membership_threshold <= 1:
        raise ValueError("membership_threshold must be greater than zero and at most one")
    selection = {
        "mode": "automatic" if components is None else "manual", "selected_components": None,
        "max_components": 0, "requested_max_components": max_components, "candidates": [],
        "criterion": "Smallest eligible K within one standard error of the best held-out reconstruction score plus (K-1)/usable decks",
        "reason": "Not enough usable card evidence to fit packages",
        "validation_note": "Three seeded 80/20 deck splits; fixed full-sample vocabulary and IDF. Reconstruction, not independent predictive validation.",
        "stability_thresholds": {"seed_consistency": 0.9, "subsample_consistency": 0.8},
    }
    kept, rejected, duplicates = prepare_decks(decks, base["commander_names"], min_cards=min_cards,
                                              commander_name_groups=base.get("commander_name_groups"))
    result = {"status": "insufficient_data", "input_decks": len(decks), "unique_decks": len(kept),
              "exact_duplicates_removed": duplicates, "rejected": rejected, "packages": [],
              "decks": [], "components_requested": components, "package_count": 0,
              "alternative_models": [], "model_selection": selection,
              "membership_threshold": membership_threshold}
    required_decks = max(8, 2 * (components or 1))
    if len(kept) < required_decks:
        result["reason"] = f"Need at least {required_decks} usable unique decks"
        selection["reason"] = result["reason"]
        return result
    basics = {"plains", "island", "swamp", "mountain", "forest", "wastes"}
    basics |= {"snow-covered " + name for name in list(basics)}
    lands = set(base["land_keys"]) | basics
    card_sets = [{key for key in deck["counts"] if key not in lands and
                  "land" not in deck["types"].get(key, "").casefold().split()}
                 for deck in kept]
    support = Counter(key for cards in card_sets for key in cards)
    vocabulary = sorted(key for key, count in support.items() if min_support <= count < 0.85 * len(kept))
    feature_mode = "varying_cards"
    if not vocabulary and (components is None or components == 1):
        # Homogeneous samples can still show a recurring package without inventing
        # separate build styles. Only use this fallback if no varying features exist.
        vocabulary = sorted(key for key, count in support.items() if count >= min_support)
        feature_mode = "shared_cards_fallback"
    if len(vocabulary) < (components or 1):
        result["reason"] = "Too few recurring cards after support and staple filters"
        selection["reason"] = result["reason"]
        return result
    use_sparse = len(kept) > 1000
    if use_sparse:
        columns = {key: index for index, key in enumerate(vocabulary)}
        positions = [(row, columns[key]) for row, cards in enumerate(card_sets) for key in cards if key in columns]
        row_indexes, column_indexes = zip(*positions)
        presence = sparse.csr_matrix((np.ones(len(positions)), (row_indexes, column_indexes)),
                                     shape=(len(kept), len(vocabulary)))
    else:
        presence = np.array([[key in cards for key in vocabulary] for cards in card_sets], dtype=float)
    informative = np.asarray(presence.sum(axis=1)).ravel() > 0
    for deck in np.array(kept, dtype=object)[~informative]:
        result["rejected"].append({"id": deck["id"], "reason": "No informative cards after feature filtering"})
    kept = [deck for deck, use in zip(kept, informative) if use]
    card_sets = [cards for cards, use in zip(card_sets, informative) if use]
    presence = presence[informative]
    result["unique_decks"] = len(kept)
    if len(kept) < required_decks:
        result["reason"] = "Too few decks with informative features"
        selection["reason"] = result["reason"]
        return result
    idf = (np.ones(len(vocabulary)) if feature_mode == "shared_cards_fallback" else
           np.log((1 + len(kept)) / (1 + np.asarray(presence.sum(axis=0)).ravel())))
    matrix = presence.multiply(idf).tocsr() if use_sparse else presence * idf
    if use_sparse:
        matrix.eliminate_zeros()
    # Columns can become universal after rejecting featureless decks. Retain a
    # single shared package if IDF then removes the entire remaining signal.
    if (not matrix.nnz if use_sparse else not matrix.any()) and (components is None or components == 1):
        idf = np.ones(len(vocabulary))
        matrix = presence.copy()
        feature_mode = "shared_cards_fallback"
    if use_sparse:
        from sklearn.utils.extmath import randomized_svd

        norm = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
        matrix = matrix.multiply((1 / np.maximum(norm, 1e-12))[:, None]).tocsr()
        # Only determine rank up to the candidate ceiling, not a full costly SVD.
        ceiling = min(max_components if components is None else components, *matrix.shape)
        _, values, _ = randomized_svd(matrix, n_components=ceiling, random_state=seed)
        rank = int((values > max(matrix.shape) * np.finfo(float).eps * values[0]).sum())
    else:
        matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
        rank = int(np.linalg.matrix_rank(matrix))
    supported_max = min(max_components, len(kept) // 4, len(vocabulary), rank)
    selection["max_components"] = supported_max
    selection["data_limits"] = {"usable_decks": len(kept), "features": len(vocabulary),
                                "matrix_rank": None if use_sparse else rank,
                                "rank_at_least": rank, "sparse_matrix": use_sparse,
                                "minimum_decks_per_candidate_component": 4}
    if rank < (components or 1):
        result["reason"] = "Too few independent card signals for the requested package count"
        selection["reason"] = result["reason"]
        return result
    counts = [components] if components is not None else range(1, supported_max + 1)
    fits = {count: _evaluate_components(matrix, count, seed, membership_threshold) for count in counts}
    selection["candidates"] = [fit["diagnostic"] for fit in fits.values()]
    if components is not None:
        chosen = components
        selection["reason"] = f"Manually requested {chosen} package(s); support and consistency diagnostics are reported separately"
    else:
        eligible = [fit["diagnostic"] for fit in fits.values() if fit["diagnostic"]["eligible"]]
        if not eligible:
            result["reason"] = "No package count met the support, consistency and convergence checks"
            selection["reason"] = result["reason"]
            return result
        best = min(eligible, key=lambda row: (row["score"], row["components"]))
        tolerance = best["validation_error_se"]
        chosen = min(row["components"] for row in eligible if row["score"] <= best["score"] + tolerance + 1e-12)
        selection["one_standard_error_tolerance"] = tolerance
        selection["reason"] = (
            f"Selected {chosen} package(s), the simplest supported count within one standard error "
            f"of the best complexity-adjusted held-out reconstruction among 1–{supported_max}. "
            "This is an exploratory heuristic, not proof of the number of strategies."
        )
        if feature_mode == "shared_cards_fallback":
            selection["reason"] = "One shared recurring-card package; the sample has no evidence for separate card packages after filtering"
    selection["selected_components"] = chosen
    fits[chosen]["diagnostic"]["selected"] = True
    vectors = enriched_vectors(base, profiles)
    summaries = {count: _package_summary(fit, kept, vocabulary, presence, idf, vectors, profiles, membership_threshold)
                 for count, fit in fits.items() if fit["diagnostic"]["eligible"] or count == chosen}
    selected = summaries[chosen]
    memberships = fits[chosen]["memberships"]
    near_duplicates = (sum(len(a & b) / len(a | b) >= 0.9 for a, b in combinations(card_sets, 2) if a | b)
                       if not use_sparse else None)
    labeled = [deck for deck in kept if deck.get("tags")]
    tag_pairs = Counter(pair for deck in labeled for pair in combinations(sorted(set(deck["tags"])), 2))
    result.update({
        **{key: value for key, value in selected.items() if key not in {"memberships", "components"}},
        "status": "fitted", "method": "IDF-weighted binary nonland cards; L2-normalized decks; NMF",
        "feature_count": len(vocabulary), "minimum_card_support": min_support,
        "feature_filter_mode": feature_mode, "staple_exclusion_fraction": 0.85,
        "package_count": chosen, "alternative_models": list(summaries.values()),
        "tag_suggestion_rule": {"minimum_cosine": 0.2, "minimum_fraction_of_best": 0.5,
                                "note": "All profiles above both heuristic similarity floors; no fixed tag count or calibrated tag confidence."},
        "consistency_note": "Matched component cosine across four initializations and three 80% training subsets; not a confidence probability.",
        "near_duplicate_pairs": near_duplicates, "decks_with_source_tags": len(labeled),
        "near_duplicate_note": "Pairwise near-copy diagnostic omitted for large collections; exact copies are still removed" if use_sparse else "All pairs checked",
        "observed_tag_pairs": [{"tags": list(pair), "count": count} for pair, count in tag_pairs.most_common(15)],
        "source_hosts": dict(Counter(urlsplit(deck.get("source_url", "")).hostname or "local export" for deck in kept)),
        "decks": [{"id": deck["id"], "source_url": deck.get("source_url", ""),
                   "preview_url": deck.get("preview_url", ""), "savedate": deck.get("savedate"),
                   "source_tags": deck.get("tags", []), "card_count": deck["card_count"],
                   "memberships": memberships[i].tolist()}
                  for i, deck in enumerate(kept)],
        "interpretation": "Relative package weights, not probabilities or population build shares. Source tags are unused during fitting; labels are profile-based suggestions.",
    })
    return result
