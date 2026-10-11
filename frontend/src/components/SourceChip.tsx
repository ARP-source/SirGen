/** A cited source_id. Clicking scrolls to the element that shows that record (id="src-<source_id>"). */
export function jumpTo(sourceId: string): void {
  const el = document.getElementById(`src-${sourceId}`)
  if (!el) return
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  el.classList.remove('flash')
  void el.offsetWidth // restart the highlight if it is already running
  el.classList.add('flash')
  window.setTimeout(() => el.classList.remove('flash'), 2200)
}

export default function SourceChip({ id }: { id: string }) {
  return (
    <a
      className="chip"
      href={`#src-${id}`}
      title={`Show ${id} in the source record`}
      onClick={(e) => {
        e.preventDefault()
        jumpTo(id)
      }}
    >
      {id}
    </a>
  )
}
