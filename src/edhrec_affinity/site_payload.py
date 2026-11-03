"""Bounded presentation projection for static build reports.

This is applied to a disposable publication copy, never to collected reports or
training data. Keep every model, deck, source link, score and card statistic.
Per-request audit history belongs in the durable checkpoint. Six decimal places
are sufficient for displayed mixture weights; retain exact values whenever
rounding would alter ordering, displayed percentages or the membership cutoff.
"""
from __future__ import annotations

import math


def _quantized(value):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
        return value
    return round(value, 6)


def _display_percentage(value):
    # JS (value * 100).toFixed(0), including values just below a half.
    # floor(x + .5) would itself round some near-half floats across the tie.
    scaled = value * 100
    whole = math.floor(scaled)
    return whole + (scaled - whole >= 0.5)


def _project_weights(rows, field, threshold):
    if not isinstance(rows, list):
        return
    originals, collisions = {}, set()
    for row in rows:
        values = row.get(field) if isinstance(row, dict) else None
        if not isinstance(values, list):
            continue
        for column, value in enumerate(values):
            rounded = _quantized(value)
            if type(rounded) not in {int, float}:
                continue
            key = column, rounded
            if key in originals and originals[key] != value:
                collisions.add(key)
            else:
                originals[key] = value
    for row in rows:
        values = row.get(field) if isinstance(row, dict) else None
        if not isinstance(values, list):
            continue
        for column, value in enumerate(values):
            rounded = _quantized(value)
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 1:
                continue
            if ((column, rounded) in collisions
                    or (value >= threshold) != (rounded >= threshold)
                    or _display_percentage(value) != _display_percentage(rounded)):
                continue
            values[column] = rounded


def project_build_report(report: dict) -> dict:
    """Project an independently loaded publication copy in place."""
    collection = report.get("collection")
    if isinstance(collection, dict):
        collection.pop("provenance", None)
    analysis = report.get("deck_analysis")
    if not isinstance(analysis, dict):
        return report
    threshold = analysis.get("membership_threshold", 0.2)
    if type(threshold) not in {int, float} or not math.isfinite(threshold):
        threshold = 0.2
    _project_weights(analysis.get("decks"), "memberships", threshold)
    for model in analysis.get("alternative_models", []):
        if isinstance(model, dict):
            _project_weights(model.get("memberships"), "weights", threshold)
    report["site_export"] = {
        "version": 1, "mixture_decimal_places": 6,
        "exact_mixture_exceptions": "Preserve membership cutoffs, displayed percentages and ordering",
        "audit_history": "Retained in the collector checkpoint",
    }
    return report
