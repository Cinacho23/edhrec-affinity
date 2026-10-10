import { loadQuirkyBuildsReport } from "./api.js";
import { expandThemeReport } from "./themeReportData.js";

const REQUIRED_FIELDS = [
  "commander_slug", "theme_affinity_z", "theme_tag_decks",
  "theme_affinity_pct", "theme_affinity_adjusted_pct",
  "theme_affinity_lower_pct", "theme_affinity_upper_pct",
  "theme_affinity_model_status", "theme_affinity_model_version",
  "theme_rank_within_tag_by_z",
];

/** Decode the complete columnar export; a z-gated report cannot supply it. */
export function expandQuirkyBuildsReport(data) {
  if (
    data?.schema_version !== 1 ||
    data?.coverage !== "all_observed_commander_tags" ||
    !Array.isArray(data.row_fields) ||
    data.row_fields.some((field) => typeof field !== "string") ||
    new Set(data.row_fields).size !== data.row_fields.length ||
    REQUIRED_FIELDS.some((field) => !data.row_fields.includes(field)) ||
    !Array.isArray(data.commanders) || !Array.isArray(data.themes)
  ) {
    throw new Error("The Quirky Builds dataset is incomplete or uses an unsupported format.");
  }

  const themes = data.themes.map((theme) => {
    if (!theme?.tag_slug || !Array.isArray(theme.rows)) {
      throw new Error("The Quirky Builds dataset contains an invalid theme.");
    }
    return {
      ...theme,
      rows: theme.rows.map((values) => {
        if (!Array.isArray(values) || values.length !== data.row_fields.length) {
          throw new Error("The Quirky Builds dataset contains an invalid build.");
        }
        return Object.fromEntries(data.row_fields.map((field, index) => [field, values[index]]));
      }),
    };
  });

  return expandThemeReport({ commanders: data.commanders, themes });
}

export function createQuirkyBuildsDataLoader({ loadReport = loadQuirkyBuildsReport } = {}) {
  let report;

  return {
    loadReport() {
      report ??= Promise.resolve().then(loadReport).then(expandQuirkyBuildsReport).catch((error) => {
        report = null;
        throw error;
      });
      return report;
    },
  };
}
