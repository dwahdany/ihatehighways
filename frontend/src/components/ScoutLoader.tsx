import { useEffect, useState } from 'react'

const PHASES = [
  'Reading live traffic…',
  'Scanning corridors for real roads…',
  'Pricing every cut…',
]

/** Road-with-passing-dashes loader shown while the backend scouts cuts. */
export default function ScoutLoader() {
  const [phase, setPhase] = useState(0)

  useEffect(() => {
    const timer = setInterval(() => setPhase((value) => (value + 1) % PHASES.length), 2200)
    return () => clearInterval(timer)
  }, [])

  // No role="status" here: the surrounding results container is already aria-live,
  // and the cycling caption is aria-hidden so screen readers hear one calm sentence
  // instead of every 2.2 s phase change (twice).
  return (
    <div className="scout-loader">
      <div className="scout-road" aria-hidden="true" />
      <p className="scout-caption" aria-hidden="true">
        {PHASES[phase]}
      </p>
      <span className="visually-hidden">Scouting cuts, this can take a moment.</span>
    </div>
  )
}
