/*
  DataTable.jsx

  A reusable table component built with TanStack Table.

  This component is intentionally "generic":
  - It does not know anything specific about EDHREC.
  - It receives rows and column definitions from each page.
  - It handles common table behavior: sorting, global search, and pagination.

  We are not adding virtualization yet. Pagination is simpler and enough for
  the first Chat 10 version. If the full affinity table feels slow later, then
  TanStack Virtual can be added as a performance upgrade.
*/

import { useState } from "react";

import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";

export default function DataTable({
  data,
  columns,
  initialSorting = [],
  pageSize = 25,
  tableLabel = "Data table",
}) {
  /*
    TanStack Table keeps table behavior in state.

    sorting:
      Tracks which column is sorted and whether it is ascending/descending.

    globalFilter:
      A simple text search applied across globally filterable columns.
      We also use custom page-level filters elsewhere, so this is just an
      additional convenience search.
  */
  const [sorting, setSorting] = useState(initialSorting);
  const [globalFilter, setGlobalFilter] = useState("");

  const table = useReactTable({
    data,
    columns,

    state: {
      sorting,
      globalFilter,
    },

    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,

    /*
      Row models are the processing steps TanStack uses.

      Core row model:
        Base table rows.

      Filtered row model:
        Rows after global/column filters.

      Sorted row model:
        Rows after sorting.

      Paginated row model:
        Current page of rows.
    */
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),

    /*
      Set a default page size when the table is created.
    */
    initialState: {
      pagination: {
        pageSize,
      },
    },
  });

  const visibleRowCount = table.getFilteredRowModel().rows.length;
  const totalRowCount = data.length;

  return (
    <section className="data-table-section" aria-label={tableLabel}>
      <div className="table-toolbar">
        <div>
          <p className="eyebrow">Rows</p>
          <p className="table-count">
            Showing {visibleRowCount.toLocaleString()} of{" "}
            {totalRowCount.toLocaleString()} loaded rows
          </p>
        </div>

        <label className="table-search">
          <span>Search visible table</span>
          <input
            type="search"
            value={globalFilter ?? ""}
            onChange={(event) => setGlobalFilter(event.target.value)}
            placeholder="Search current rows..."
          />
        </label>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sortDirection = header.column.getIsSorted();

                  return (
                    <th key={header.id}>
                      {header.isPlaceholder ? null : (
                        <button
                          type="button"
                          className={canSort ? "table-header-button" : "table-header-static"}
                          onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                          disabled={!canSort}
                        >
                          {flexRender(
                            header.column.columnDef.header,
                            header.getContext()
                          )}

                          {sortDirection === "asc" && (
                            <span aria-label="sorted ascending"> ▲</span>
                          )}

                          {sortDirection === "desc" && (
                            <span aria-label="sorted descending"> ▼</span>
                          )}
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>

          <tbody>
            {table.getRowModel().rows.length > 0 ? (
              table.getRowModel().rows.map((row) => (
                <tr key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={columns.length} className="empty-cell">
                  No rows match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pagination-bar">
        <button
          type="button"
          onClick={() => table.firstPage()}
          disabled={!table.getCanPreviousPage()}
        >
          First
        </button>

        <button
          type="button"
          onClick={() => table.previousPage()}
          disabled={!table.getCanPreviousPage()}
        >
          Previous
        </button>

        <span>
          Page{" "}
          <strong>
            {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
          </strong>
        </span>

        <button
          type="button"
          onClick={() => table.nextPage()}
          disabled={!table.getCanNextPage()}
        >
          Next
        </button>

        <button
          type="button"
          onClick={() => table.lastPage()}
          disabled={!table.getCanNextPage()}
        >
          Last
        </button>

        <label>
          <span>Rows per page</span>
          <select
            value={table.getState().pagination.pageSize}
            onChange={(event) => table.setPageSize(Number(event.target.value))}
          >
            {[10, 25, 50, 100].map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>
    </section>
  );
}