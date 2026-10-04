// Expand the shared commander metadata once per theme, so the report can use
// the same classifier as Theme Brackets without downloading full tag records.
export function expandThemeReport(data) {
  if (!Array.isArray(data?.commanders) || !Array.isArray(data?.themes)) {
    throw new Error("The theme report dataset is invalid.");
  }

  const commanders = new Map(
    data.commanders.map((commander) => [commander.commander_slug, commander])
  );

  return data.themes.map((theme) => {
    if (!theme.tag_slug || !Array.isArray(theme.rows)) {
      throw new Error("The theme report contains an invalid theme.");
    }

    return {
      ...theme,
      rows: theme.rows.map((row) => {
        const commander = commanders.get(row.commander_slug);

        if (!commander || !Array.isArray(commander.bracket_tag_rows)) {
          throw new Error(`Missing bracket data for ${row.commander_slug}.`);
        }

        return {
          ...commander,
          ...row,
          theme_tag_slug: theme.tag_slug,
          theme_tag_name: theme.tag_name || theme.tag_slug,
        };
      }),
    };
  });
}
