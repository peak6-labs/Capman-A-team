interface SideBadgeProps {
  side: string | null | undefined
  uppercase?: boolean
}

export function SideBadge({ side, uppercase = false }: SideBadgeProps) {
  if (!side) return <span className="muted">—</span>
  const isYes = side.toLowerCase() === 'yes'
  return (
    <span className={`side-badge ${isYes ? 'yes' : 'no'}`}>
      {uppercase ? side.toUpperCase() : side}
    </span>
  )
}

export function PositionSideBadge({ position }: { position: string | null | undefined }) {
  if (position == null) return <span className="muted">—</span>

  const n = parseFloat(position)
  if (isNaN(n)) return <span>{position}</span>

  const isYes = n >= 0
  return (
    <span className={`side-badge ${isYes ? 'yes' : 'no'}`}>
      {isYes ? 'Yes' : 'No'} · {Math.abs(n)}
    </span>
  )
}

export function ActionBadge({ action }: { action: string | null | undefined }) {
  if (!action) return <span className="muted">—</span>
  return <span className="badge badge-gray">{action}</span>
}
