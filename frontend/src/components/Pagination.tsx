// One pagination control, used identically on every list (top and bottom).
export default function Pagination({
  page,
  pageCount,
  total,
  pageSize,
  onPage,
}: {
  page: number
  pageCount: number
  total: number
  pageSize: number
  onPage: (page: number) => void
}) {
  if (total === 0) return null
  const start = page * pageSize + 1
  const end = Math.min((page + 1) * pageSize, total)
  return (
    <div className="pagination">
      <span className="page-range mono">{start}-{end} of {total}</span>
      <div className="page-controls">
        <button className="ghost" disabled={page <= 0} onClick={() => onPage(Math.max(0, page - 1))}>
          Prev
        </button>
        <span className="page-indicator mono">Page {page + 1} / {pageCount}</span>
        <button className="ghost" disabled={page >= pageCount - 1} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}>
          Next
        </button>
      </div>
    </div>
  )
}
