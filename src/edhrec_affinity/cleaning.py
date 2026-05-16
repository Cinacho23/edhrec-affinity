"""
cleaning.py

Chat 6 - Data Cleaning and Validation

This file turns raw scraper output into clean, validated, analysis-ready data.

Inputs:
- commander_index.json
- commander_tags_raw.jsonl or commander_tags_raw.json
- commander_tags_cedh_raw.jsonl or commander_tags_cedh_raw.json
- optional commander_cedh_status.jsonl or commander_cedh_status.json

Outputs:
- commanders_clean.json
- commander_tags_clean.json
- tags_clean.json
- data_validation_report.json
- invalid_commander_tag_rows.json
- duplicate_conflict_rows.json
- exact_duplicate_rows.json

Important project rule:
- Chat 6 does NOT calculate z-scores, ranks, or percentiles.
- Chat 6 only cleans and validates the raw data.
- Chat 7 will calculate statistics from the clean outputs.

Normal tag rows:
    source_type = "commander_json"

cEDH tag rows:
    source_type = "cedh_filtered_json"
    tag_slug = "cedh"
    tag_name = "cEDH"

The cEDH row should already have:
    tag_decks = cEDH JSON num_decks_avg
    total_decks = normal commander total deck count
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


JsonDict = dict[str, Any]


# These are the required fields for every commander-tag row.
# Both normal tags and cEDH tags must fit this shape.
CLEAN_TAG_COLUMNS = [
    "commander_name",
    "commander_slug",
    "total_decks",
    "tag_name",
    "tag_slug",
    "tag_decks",
    "source_type",
    "scrape_timestamp",
]


# This key defines one unique commander-tag-source row.
#
# source_type is included because a normal EDHREC tag and a special filtered
# source could theoretically have the same slug. For cEDH, source_type also
# makes it clear that the row came from the special cEDH route.
SEMANTIC_KEY_COLUMNS = [
    "commander_slug",
    "tag_slug",
    "source_type",
]


ALLOWED_SOURCE_TYPES = {
    "commander_json",
    "cedh_filtered_json",
}


# Output filenames for Chat 6.
COMMANDER_TAGS_CLEAN_JSON = "commander_tags_clean.json"
COMMANDERS_CLEAN_JSON = "commanders_clean.json"
TAGS_CLEAN_JSON = "tags_clean.json"
VALIDATION_REPORT_JSON = "data_validation_report.json"
INVALID_ROWS_JSON = "invalid_commander_tag_rows.json"
DUPLICATE_CONFLICT_ROWS_JSON = "duplicate_conflict_rows.json"
EXACT_DUPLICATE_ROWS_JSON = "exact_duplicate_rows.json"


class CleanCommanderTagRow(BaseModel):
    """
    Pydantic model for one cleaned commander-tag row.

    This validates one row at a time.

    pandas will then handle table-level validation, such as:
    - duplicate detection
    - grouping commanders
    - grouping tags
    - source_type counts
    """

    # Ignore extra fields from raw files.
    # This makes the cleaner tolerant if future scrapers add fields like URL.
    model_config = ConfigDict(extra="ignore")

    commander_name: str
    commander_slug: str
    total_decks: int = Field(gt=0)
    tag_name: str
    tag_slug: str
    tag_decks: int = Field(ge=0)
    source_type: str
    scrape_timestamp: str

    @field_validator(
        "commander_name",
        "commander_slug",
        "tag_name",
        "tag_slug",
        "source_type",
        "scrape_timestamp",
    )
    @classmethod
    def strip_and_reject_blank_strings(cls, value: str) -> str:
        """
        Strip whitespace and reject blank strings.

        This prevents values like:
            "  tokens  "
        or:
            ""
        from getting into the clean data.
        """
        value = str(value).strip()

        if not value:
            raise ValueError("field cannot be blank")

        return value

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        """
        Validate the source_type.

        For Chat 6, we expect:
        - commander_json
        - cedh_filtered_json
        """
        if value not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"unexpected source_type: {value}")

        return value

    @field_validator("scrape_timestamp")
    @classmethod
    def validate_iso_timestamp(cls, value: str) -> str:
        """
        Check that scrape_timestamp is parseable as an ISO-style datetime.

        We keep it as a string in the output because JSON has no native datetime
        type, but validating it here catches broken timestamps early.
        """
        normalized = value.replace("Z", "+00:00")

        try:
            datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"invalid ISO timestamp: {value}") from exc

        return value

    @model_validator(mode="after")
    def validate_deck_count_relationship(self) -> "CleanCommanderTagRow":
        """
        Validate count relationships that involve multiple fields.

        A tag count should not exceed total commander decks.

        Example invalid row:
            total_decks = 100
            tag_decks = 150
        """
        if self.tag_decks > self.total_decks:
            raise ValueError(
                f"tag_decks ({self.tag_decks}) cannot exceed total_decks ({self.total_decks})"
            )

        if self.source_type == "cedh_filtered_json":
            if self.tag_slug != "cedh":
                raise ValueError("cEDH rows must use tag_slug='cedh'")

            if self.tag_name != "cEDH":
                raise ValueError("cEDH rows must use tag_name='cEDH'")

        return self


def read_json_records(path: Path, *, required: bool = True) -> list[JsonDict]:
    """
    Read JSON records from a .json or .jsonl file.

    Supported input shapes:
    - JSONL:
        one JSON object per line

    - JSON:
        [
          {...},
          {...}
        ]

    - wrapped JSON:
        {"records": [...]}
        {"commanders": [...]}
        {"rows": [...]}
        {"data": [...]}

    Args:
        path:
            Input path.
        required:
            If True, missing file raises FileNotFoundError.
            If False, missing file returns an empty list.

    Returns:
        A list of dictionaries.
    """
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Input file does not exist: {path}")
        return []

    if path.suffix == ".jsonl":
        records: list[JsonDict] = []

        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                line = line.strip()

                if not line:
                    continue

                value = json.loads(line)

                if not isinstance(value, dict):
                    raise ValueError(
                        f"Expected JSON object on line {line_number} in {path}"
                    )

                records.append(value)

        return records

    data = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("records", "commanders", "rows", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    raise ValueError(f"Unsupported JSON shape in {path}")


def write_json_records(path: Path, records: list[JsonDict]) -> None:
    """
    Write a list of dictionaries as pretty JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_json_object(path: Path, record: JsonDict) -> None:
    """
    Write one dictionary as pretty JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_count(value: Any) -> Any:
    """
    Normalize integer-like count values before Pydantic validation.

    This supports:
    - 123
    - 123.0
    - "123"
    - "1,234"

    If the value is invalid, return it unchanged and let Pydantic raise a
    clear validation error later.
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")

        if cleaned == "":
            return value

        try:
            return int(cleaned)
        except ValueError:
            return value

    return value


def normalize_raw_tag_record(raw_record: JsonDict) -> JsonDict:
    """
    Normalize one raw commander-tag record before schema validation.

    This function does small, safe fixes:
    - strips whitespace from string fields
    - converts count-like strings into integers
    - forces cEDH rows to use tag_slug="cedh" and tag_name="cEDH"

    It does not silently repair serious errors like missing commander_slug or
    tag_decks > total_decks. Those should be reported.
    """
    normalized: JsonDict = {}

    for column in CLEAN_TAG_COLUMNS:
        normalized[column] = raw_record.get(column)

    for column in (
        "commander_name",
        "commander_slug",
        "tag_name",
        "tag_slug",
        "source_type",
        "scrape_timestamp",
    ):
        value = normalized.get(column)

        if value is not None:
            normalized[column] = str(value).strip()

    normalized["total_decks"] = normalize_count(normalized.get("total_decks"))
    normalized["tag_decks"] = normalize_count(normalized.get("tag_decks"))

    # cEDH is a synthetic tag row. Keep this consistent even if a raw cEDH
    # scraper accidentally used lowercase or inconsistent display text.
    if normalized.get("source_type") == "cedh_filtered_json":
        normalized["tag_slug"] = "cedh"
        normalized["tag_name"] = "cEDH"

    return normalized


def tag_records_with_input_source(
    records: list[JsonDict],
    *,
    input_source: str,
) -> list[JsonDict]:
    """
    Add internal source metadata to raw records.

    This field is not part of the clean output.
    It only helps the validation report explain where invalid records came from.
    """
    tagged_records: list[JsonDict] = []

    for index, record in enumerate(records, start=1):
        copied = dict(record)
        copied["_input_source"] = input_source
        copied["_input_row_number"] = index
        tagged_records.append(copied)

    return tagged_records


def get_known_commander_slugs(commander_index_records: list[JsonDict]) -> set[str]:
    """
    Extract commander slugs from commander_index.json.

    Chat 6 uses this to verify that every cleaned commander-tag row traces
    back to the discovered commander universe from Chat 4.
    """
    known_slugs: set[str] = set()

    for record in commander_index_records:
        slug = str(record.get("commander_slug", "")).strip()

        if slug:
            known_slugs.add(slug)

    return known_slugs


def validate_and_clean_tag_rows(
    raw_records: list[JsonDict],
    *,
    known_commander_slugs: set[str],
) -> tuple[pd.DataFrame, list[JsonDict], list[JsonDict], list[JsonDict], JsonDict]:
    """
    Validate and clean raw commander-tag rows.

    Returns:
        clean_df:
            DataFrame of clean rows.

        invalid_rows:
            Raw rows that failed schema validation or commander-index validation.

        conflict_rows:
            Rows with duplicate semantic keys but conflicting values.

        exact_duplicate_rows:
            Exact duplicates that were dropped.

        stats:
            Counts useful for the validation report.
    """
    valid_rows: list[JsonDict] = []
    invalid_rows: list[JsonDict] = []

    schema_invalid_count = 0
    unknown_commander_slug_count = 0

    for global_row_number, raw_record in enumerate(raw_records, start=1):
        normalized = normalize_raw_tag_record(raw_record)

        try:
            clean_model = CleanCommanderTagRow.model_validate(normalized)

        except ValidationError as exc:
            schema_invalid_count += 1
            invalid_rows.append(
                {
                    "global_row_number": global_row_number,
                    "input_source": raw_record.get("_input_source"),
                    "input_row_number": raw_record.get("_input_row_number"),
                    "reason_type": "schema_validation_error",
                    "reason": str(exc),
                    "raw_record": raw_record,
                }
            )
            continue

        clean_record = clean_model.model_dump()

        if clean_record["commander_slug"] not in known_commander_slugs:
            unknown_commander_slug_count += 1
            invalid_rows.append(
                {
                    "global_row_number": global_row_number,
                    "input_source": raw_record.get("_input_source"),
                    "input_row_number": raw_record.get("_input_row_number"),
                    "reason_type": "unknown_commander_slug",
                    "reason": (
                        "commander_slug was not found in commander_index.json: "
                        f"{clean_record['commander_slug']}"
                    ),
                    "raw_record": raw_record,
                }
            )
            continue

        valid_rows.append(clean_record)

    if not valid_rows:
        clean_df = pd.DataFrame(columns=CLEAN_TAG_COLUMNS)
    else:
        clean_df = pd.DataFrame(valid_rows, columns=CLEAN_TAG_COLUMNS)

    rows_before_duplicate_handling = int(len(clean_df))

    # Exact duplicate rows are safe to drop because every clean output field
    # is identical.
    exact_duplicate_mask = clean_df.duplicated(
        subset=CLEAN_TAG_COLUMNS,
        keep="first",
    )

    exact_duplicate_rows = clean_df.loc[exact_duplicate_mask].to_dict("records")

    clean_df = clean_df.drop_duplicates(
        subset=CLEAN_TAG_COLUMNS,
        keep="first",
    ).copy()

    # Conflicting duplicate rows are more serious:
    # same commander_slug + tag_slug + source_type, but different values.
    #
    # Example:
    #   same commander/tag/source, but tag_decks differs.
    conflict_mask = clean_df.duplicated(
        subset=SEMANTIC_KEY_COLUMNS,
        keep=False,
    )

    conflict_rows = (
        clean_df.loc[conflict_mask]
        .sort_values(SEMANTIC_KEY_COLUMNS)
        .to_dict("records")
    )

    if conflict_rows:
        conflicting_duplicate_key_count = int(
            clean_df.loc[conflict_mask, SEMANTIC_KEY_COLUMNS]
            .drop_duplicates()
            .shape[0]
        )

        # Keep the first row per semantic key so the clean table remains usable,
        # but preserve all conflict rows in duplicate_conflict_rows.json.
        clean_df = clean_df.drop_duplicates(
            subset=SEMANTIC_KEY_COLUMNS,
            keep="first",
        ).copy()
    else:
        conflicting_duplicate_key_count = 0

    if not clean_df.empty:
        clean_df = clean_df.sort_values(
            by=["commander_slug", "source_type", "tag_slug"],
            kind="stable",
        ).reset_index(drop=True)

    stats: JsonDict = {
        "rows_before_schema_validation": int(len(raw_records)),
        "schema_invalid_row_count": int(schema_invalid_count),
        "unknown_commander_slug_count": int(unknown_commander_slug_count),
        "valid_row_count_before_duplicate_handling": int(rows_before_duplicate_handling),
        "dropped_exact_duplicate_row_count": int(len(exact_duplicate_rows)),
        "conflicting_duplicate_key_count": int(conflicting_duplicate_key_count),
        "conflicting_duplicate_row_count": int(len(conflict_rows)),
        "clean_tag_row_count": int(len(clean_df)),
    }

    return clean_df, invalid_rows, conflict_rows, exact_duplicate_rows, stats


def build_commanders_table(clean_tags_df: pd.DataFrame) -> list[JsonDict]:
    """
    Build commanders_clean.json.

    One row per commander.

    This table is useful for:
    - commander search page
    - commander detail page
    - summary counts
    - later joining with card image data
    """
    if clean_tags_df.empty:
        return []

    grouped = (
        clean_tags_df.groupby("commander_slug", as_index=False)
        .agg(
            commander_name=("commander_name", "first"),
            total_decks=("total_decks", "max"),
            tag_count=("tag_slug", "count"),
            normal_tag_count=(
                "source_type",
                lambda values: int((values == "commander_json").sum()),
            ),
            has_cedh_tag=(
                "source_type",
                lambda values: bool((values == "cedh_filtered_json").any()),
            ),
            source_types=(
                "source_type",
                lambda values: sorted(set(values)),
            ),
            first_scrape_timestamp=("scrape_timestamp", "min"),
            latest_scrape_timestamp=("scrape_timestamp", "max"),
        )
    )

    cedh_counts = (
        clean_tags_df.loc[clean_tags_df["source_type"] == "cedh_filtered_json"]
        .set_index("commander_slug")["tag_decks"]
        .to_dict()
    )

    grouped["cedh_decks"] = (
        grouped["commander_slug"].map(cedh_counts).fillna(0).astype(int)
    )

    grouped = grouped.sort_values(
        by=["commander_name", "commander_slug"],
        kind="stable",
    ).reset_index(drop=True)

    return grouped.to_dict("records")


def build_tags_table(clean_tags_df: pd.DataFrame) -> list[JsonDict]:
    """
    Build tags_clean.json.

    One row per tag.

    This table is useful for:
    - tag explorer page
    - validating tag coverage
    - showing how many commanders have each tag
    """
    if clean_tags_df.empty:
        return []

    grouped = (
        clean_tags_df.groupby("tag_slug", as_index=False)
        .agg(
            tag_name=("tag_name", "first"),
            commander_count=("commander_slug", "nunique"),
            total_tag_decks=("tag_decks", "sum"),
            source_types=(
                "source_type",
                lambda values: sorted(set(values)),
            ),
        )
    )

    grouped = grouped.sort_values(
        by=["tag_name", "tag_slug"],
        kind="stable",
    ).reset_index(drop=True)

    return grouped.to_dict("records")


def count_status_types(path: Path | None) -> dict[str, int]:
    """
    Count status_type values from a status JSON/JSONL file.

    This is useful for cEDH status output.

    The cEDH status file is not cleaned into commander-tag rows, but its
    counts should appear in the validation report so we can audit why some
    cEDH routes did not become clean rows.
    """
    if path is None:
        return {}

    records = read_json_records(path, required=False)

    counter: Counter[str] = Counter()

    for record in records:
        status_type = str(record.get("status_type", "unknown")).strip() or "unknown"
        counter[status_type] += 1

    return dict(sorted(counter.items()))


def build_validation_report(
    *,
    commander_index_path: Path,
    normal_tags_path: Path,
    cedh_tags_path: Path | None,
    cedh_status_path: Path | None,
    normal_input_count: int,
    cedh_input_count: int,
    commander_index_count: int,
    known_commander_slug_count: int,
    clean_tags_df: pd.DataFrame,
    invalid_rows: list[JsonDict],
    conflict_rows: list[JsonDict],
    exact_duplicate_rows: list[JsonDict],
    cleaning_stats: JsonDict,
) -> JsonDict:
    """
    Build the Chat 6 validation report.

    This report should be read before moving to Chat 7.
    """
    if clean_tags_df.empty:
        source_type_counts: dict[str, int] = {}
        unique_commander_count = 0
        unique_tag_count = 0
        cedh_clean_row_count = 0
    else:
        source_type_counts = {
            str(key): int(value)
            for key, value in clean_tags_df["source_type"].value_counts().to_dict().items()
        }
        unique_commander_count = int(clean_tags_df["commander_slug"].nunique())
        unique_tag_count = int(clean_tags_df["tag_slug"].nunique())
        cedh_clean_row_count = int(
            (clean_tags_df["source_type"] == "cedh_filtered_json").sum()
        )

    warnings: list[str] = []

    if invalid_rows:
        warnings.append(
            "Some rows failed validation. See invalid_commander_tag_rows.json."
        )

    if conflict_rows:
        warnings.append(
            "Some duplicate commander/tag/source keys had conflicting values. "
            "See duplicate_conflict_rows.json."
        )

    cedh_status_counts = count_status_types(cedh_status_path)

    cedh_error_status_count = sum(
        count
        for status_type, count in cedh_status_counts.items()
        if "error" in status_type or "parse" in status_type
    )

    if cedh_error_status_count:
        warnings.append(
            "cEDH status output contains error/parse statuses. "
            "The valid cEDH rows were still cleaned, but inspect commander_cedh_status.json."
        )

    report: JsonDict = {
        "commander_index_path": str(commander_index_path),
        "normal_tags_path": str(normal_tags_path),
        "cedh_tags_path": str(cedh_tags_path) if cedh_tags_path else None,
        "cedh_status_path": str(cedh_status_path) if cedh_status_path else None,
        "commander_index_record_count": int(commander_index_count),
        "known_commander_slug_count": int(known_commander_slug_count),
        "normal_input_row_count": int(normal_input_count),
        "cedh_input_row_count": int(cedh_input_count),
        "total_input_row_count": int(normal_input_count + cedh_input_count),
        "clean_tag_row_count": int(len(clean_tags_df)),
        "invalid_row_count": int(len(invalid_rows)),
        "exact_duplicate_row_count": int(len(exact_duplicate_rows)),
        "conflicting_duplicate_row_count": int(len(conflict_rows)),
        "unique_commander_count_in_clean_rows": unique_commander_count,
        "unique_tag_count_in_clean_rows": unique_tag_count,
        "cedh_clean_row_count": cedh_clean_row_count,
        "source_type_counts": source_type_counts,
        "cedh_status_type_counts": cedh_status_counts,
        "cleaning_stats": cleaning_stats,
        "warnings": warnings,
    }

    return report


def run_cleaning(
    *,
    commander_index_path: Path,
    normal_tags_path: Path,
    output_dir: Path,
    cedh_tags_path: Path | None = None,
    cedh_status_path: Path | None = None,
) -> JsonDict:
    """
    Run the complete Chat 6 cleaning step.

    This function is used by:
    - the command-line interface
    - pytest tests
    - future GitHub Actions automation
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    commander_index_records = read_json_records(commander_index_path, required=True)
    known_commander_slugs = get_known_commander_slugs(commander_index_records)

    normal_records = read_json_records(normal_tags_path, required=True)

    if cedh_tags_path is None:
        cedh_records: list[JsonDict] = []
    else:
        cedh_records = read_json_records(cedh_tags_path, required=False)

    tagged_normal_records = tag_records_with_input_source(
        normal_records,
        input_source="normal_tags",
    )

    tagged_cedh_records = tag_records_with_input_source(
        cedh_records,
        input_source="cedh_tags",
    )

    combined_raw_records = tagged_normal_records + tagged_cedh_records

    (
        clean_tags_df,
        invalid_rows,
        conflict_rows,
        exact_duplicate_rows,
        cleaning_stats,
    ) = validate_and_clean_tag_rows(
        combined_raw_records,
        known_commander_slugs=known_commander_slugs,
    )

    clean_tag_rows = clean_tags_df.to_dict("records")
    commander_rows = build_commanders_table(clean_tags_df)
    tag_rows = build_tags_table(clean_tags_df)

    validation_report = build_validation_report(
        commander_index_path=commander_index_path,
        normal_tags_path=normal_tags_path,
        cedh_tags_path=cedh_tags_path,
        cedh_status_path=cedh_status_path,
        normal_input_count=len(normal_records),
        cedh_input_count=len(cedh_records),
        commander_index_count=len(commander_index_records),
        known_commander_slug_count=len(known_commander_slugs),
        clean_tags_df=clean_tags_df,
        invalid_rows=invalid_rows,
        conflict_rows=conflict_rows,
        exact_duplicate_rows=exact_duplicate_rows,
        cleaning_stats=cleaning_stats,
    )

    write_json_records(output_dir / COMMANDER_TAGS_CLEAN_JSON, clean_tag_rows)
    write_json_records(output_dir / COMMANDERS_CLEAN_JSON, commander_rows)
    write_json_records(output_dir / TAGS_CLEAN_JSON, tag_rows)
    write_json_records(output_dir / INVALID_ROWS_JSON, invalid_rows)
    write_json_records(output_dir / DUPLICATE_CONFLICT_ROWS_JSON, conflict_rows)
    write_json_records(output_dir / EXACT_DUPLICATE_ROWS_JSON, exact_duplicate_rows)
    write_json_object(output_dir / VALIDATION_REPORT_JSON, validation_report)

    return validation_report


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Clean and validate EDHREC commander-tag raw scrape data."
    )

    parser.add_argument(
        "--commander-index",
        required=True,
        type=Path,
        help="Path to commander_index.json from Chat 4.",
    )

    parser.add_argument(
        "--normal-tags",
        required=True,
        type=Path,
        help="Path to commander_tags_raw.jsonl or commander_tags_raw.json from Chat 5.",
    )

    parser.add_argument(
        "--cedh-tags",
        required=False,
        type=Path,
        default=None,
        help="Optional path to commander_tags_cedh_raw.jsonl or .json.",
    )

    parser.add_argument(
        "--cedh-status",
        required=False,
        type=Path,
        default=None,
        help="Optional path to commander_cedh_status.jsonl or .json.",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Processed output directory, e.g. data/processed/2026-05-07.",
    )

    return parser.parse_args()


def main() -> None:
    """
    Command-line entry point.
    """
    args = parse_args()

    report = run_cleaning(
        commander_index_path=args.commander_index,
        normal_tags_path=args.normal_tags,
        cedh_tags_path=args.cedh_tags,
        cedh_status_path=args.cedh_status,
        output_dir=args.output_dir,
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()