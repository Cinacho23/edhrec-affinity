export default function SimpleTable({
  columns,
  rows,
  emptyMessage = "No rows found.",
}) {
  if (!rows || rows.length === 0) {
    return <p className="muted">{emptyMessage}</p>;
  }

  return (
    <div className="simple-table-wrap">
      <table className="simple-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key}>{column.header}</th>
            ))}
          </tr>
        </thead>

        <tbody>
          {rows.map((row, rowIndex) => (
            <tr
              key={
                row.id ||
                `${row.commander_slug || "row"}-${row.tag_slug || rowIndex}-${rowIndex}`
              }
            >
              {columns.map((column) => (
                <td key={column.key}>
                  {column.render ? column.render(row, rowIndex) : row[column.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}