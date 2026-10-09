"""Reliability-aware affinity within the observed commander-tag cohort.

The reference population is reported commander-tag rows with at least 200
total decks. Missing tags are not zero-filled. In particular, this model does
not estimate a tag's prevalence across every EDHREC deck or correct unknown
source reporting thresholds. Posterior intervals condition on fitted prior
parameters and a binomial observation model; they are approximate, not a
claim of calibrated uncertainty for overlapping or selected EDHREC decks.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import betaln, digamma, expit, logit, xlog1py, xlogy
from scipy.stats import beta


AFFINITY_MODEL_VERSION = "beta_binomial_v1"
REFERENCE_MIN_TOTAL_DECKS = 200
MIN_REFERENCE_COMMANDERS = 5
MIN_PRIOR_MEAN = 1e-9
MIN_PRIOR_STRENGTH = 1e-3
MAX_PRIOR_STRENGTH = 1e6


@dataclass(frozen=True)
class TagPrior:
    mean: float
    strength: float

    @property
    def alpha(self) -> float:
        return self.mean * self.strength

    @property
    def beta(self) -> float:
        return (1.0 - self.mean) * self.strength

    @property
    def std(self) -> float:
        """Between-commander variation, excluding binomial sampling noise."""
        return float(np.sqrt(self.mean * (1.0 - self.mean) / (self.strength + 1.0)))


def fit_tag_prior(tag_decks: np.ndarray, total_decks: np.ndarray) -> TagPrior | None:
    """Fit a beta-binomial prior by bounded marginal maximum likelihood.

    Each commander contributes one marginal likelihood, rather than pooling
    all decks into a popularity-weighted mean. Three starts reduce sensitivity
    to initialization. A boundary at maximum strength indicates insufficient
    identifiable heterogeneity; use the explicit legacy fallback in that case.
    """
    successes = np.asarray(tag_decks, dtype=float)
    trials = np.asarray(total_decks, dtype=float)
    failures = trials - successes
    proportions = successes / trials
    initial_mean = float(np.clip(proportions.mean(), MIN_PRIOR_MEAN, 1 - MIN_PRIOR_MEAN))
    # Correct the starting variance for observation noise. These moments only
    # initialize the optimizer; they do not determine the fitted parameters.
    observation_variance = float(np.mean(proportions * (1 - proportions) / trials))
    between_variance = float(np.var(proportions, ddof=1)) - observation_variance
    moment_strength = (
        initial_mean * (1 - initial_mean) / between_variance - 1
        if between_variance > 0 else MAX_PRIOR_STRENGTH / 10
    )
    moment_strength = float(np.clip(moment_strength, MIN_PRIOR_STRENGTH, MAX_PRIOR_STRENGTH))
    # Subtract a parameter-independent binomial log likelihood to keep the
    # objective near zero even for very large deck counts. Its derivative is 0.
    offset = xlogy(successes, proportions) + xlog1py(failures, -proportions)

    def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
        mean = expit(parameters[0])
        strength = np.exp(parameters[1])
        alpha = mean * strength
        beta_param = (1 - mean) * strength
        log_likelihood = betaln(successes + alpha, failures + beta_param) - betaln(alpha, beta_param)
        common = digamma(strength) - digamma(trials + strength)
        derivative_alpha = digamma(successes + alpha) - digamma(alpha) + common
        derivative_beta = digamma(failures + beta_param) - digamma(beta_param) + common
        gradient = -np.array([
            np.mean(strength * mean * (1 - mean) * (derivative_alpha - derivative_beta)),
            np.mean(alpha * derivative_alpha + beta_param * derivative_beta),
        ])
        return -float(np.mean(log_likelihood - offset)), gradient

    bounds = [
        (float(logit(MIN_PRIOR_MEAN)), float(logit(1 - MIN_PRIOR_MEAN))),
        (float(np.log(MIN_PRIOR_STRENGTH)), float(np.log(MAX_PRIOR_STRENGTH))),
    ]
    candidates = []
    for strength in dict.fromkeys([moment_strength, 1.0, 100.0]):
        result = minimize(
            objective,
            np.array([logit(initial_mean), np.log(strength)]),
            method="L-BFGS-B",
            jac=True,
            bounds=bounds,
            options={"maxiter": 300, "ftol": 1e-12, "gtol": 1e-7},
        )
        if result.success and np.isfinite(result.fun) and np.isfinite(result.x).all():
            candidates.append(result)

    if not candidates:
        return None

    best = min(candidates, key=lambda candidate: candidate.fun)
    fitted = TagPrior(mean=float(expit(best.x[0])), strength=float(np.exp(best.x[1])))
    if (
        not np.isfinite(fitted.std)
        or fitted.std <= 0
        or fitted.strength >= MAX_PRIOR_STRENGTH * (1 - 1e-5)
    ):
        return None
    return fitted


def add_reliability_adjusted_affinity(df: pd.DataFrame) -> pd.DataFrame:
    """Preserve raw/legacy metrics and replace z for successfully fitted tags.

    Sparse, invariant, and failed groups retain legacy z and raw affinity.
    No intervals or prior parameters are fabricated for these fallbacks.
    """
    result = df.copy()
    result["legacy_z"] = result["z"]
    result["affinity_model_version"] = AFFINITY_MODEL_VERSION
    result["tag_affinity_adjusted_pct"] = result["tag_affinity_pct"]
    for column in [
        "tag_affinity_lower_pct", "tag_affinity_upper_pct", "tag_prior_mean_pct",
        "tag_prior_strength", "tag_prior_std_pct",
    ]:
        result[column] = np.nan
    result["affinity_model_status"] = "fallback_fit_failed"
    result["tag_reference_row_count"] = 0

    for _, group in result.groupby("tag_slug", sort=False):
        indexes = group.index
        reference = group.loc[group["total_decks"] >= REFERENCE_MIN_TOTAL_DECKS]
        reference = reference.drop_duplicates(subset=["commander_slug"])
        result.loc[indexes, "tag_reference_row_count"] = len(reference)
        if group["tag_affinity_pct"].nunique() <= 1:
            result.loc[indexes, "affinity_model_status"] = "fallback_invariant"
            # Identical proportions have no meaningful descriptive deviation.
            result.loc[indexes, "z"] = np.nan
            continue
        if len(reference) < MIN_REFERENCE_COMMANDERS:
            result.loc[indexes, "affinity_model_status"] = "fallback_sparse"
            continue
        if reference["tag_affinity_pct"].nunique() <= 1:
            result.loc[indexes, "affinity_model_status"] = "fallback_invariant"
            continue

        try:
            prior = fit_tag_prior(reference["tag_decks"].to_numpy(), reference["total_decks"].to_numpy())
            if prior is None:
                continue
            alpha = group["tag_decks"].to_numpy(dtype=float) + prior.alpha
            beta_param = (group["total_decks"] - group["tag_decks"]).to_numpy(dtype=float) + prior.beta
            adjusted = alpha / (alpha + beta_param)
            lower = beta.ppf(0.025, alpha, beta_param)
            upper = beta.ppf(0.975, alpha, beta_param)
            scores = (adjusted - prior.mean) / prior.std
            if not np.isfinite(np.concatenate([adjusted, lower, upper, scores])).all():
                continue
        except (ValueError, FloatingPointError, OverflowError):
            continue

        result.loc[indexes, "affinity_model_status"] = "fitted"
        result.loc[indexes, "tag_affinity_adjusted_pct"] = adjusted
        result.loc[indexes, "tag_affinity_lower_pct"] = lower
        result.loc[indexes, "tag_affinity_upper_pct"] = upper
        result.loc[indexes, "tag_prior_mean_pct"] = prior.mean
        result.loc[indexes, "tag_prior_strength"] = prior.strength
        result.loc[indexes, "tag_prior_std_pct"] = prior.std
        result.loc[indexes, "z"] = scores

    return result
