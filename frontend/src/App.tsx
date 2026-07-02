import { useState } from 'react'
import type { FormEvent } from 'react'
import { APIProvider } from '@vis.gl/react-google-maps'
import { ApiError, planRoute } from './api'
import type { PlanResponse } from './api'
import MapView from './components/MapView'
import PlaceField from './components/PlaceField'
import type { PlaceSelection } from './components/PlaceField'
import BudgetSlider from './components/BudgetSlider'
import TradePanel from './components/TradePanel'
import SetupCard from './components/SetupCard'

const API_KEY: string | undefined = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

const ERROR_UPSTREAM = "Google couldn't route that. Try different points."
const ERROR_NETWORK = "Backend isn't reachable. Is it running on :8000?"
const ERROR_BACKEND = 'The backend hit an unexpected error. Try again in a moment.'
const ERROR_INVALID = "The backend rejected that request. Adjust start, end, or budget and try again."
const ERROR_GEOCODE = "Couldn't pin down one of those places. Pick start and end from the suggestions."

/** Map a planRoute failure to rider-facing copy (error codes per docs/api.md). */
function describeError(err: unknown): string {
  if (!(err instanceof ApiError)) return ERROR_NETWORK // fetch itself failed
  switch (err.code) {
    case 'INVALID_INPUT':
      return ERROR_INVALID // backend echoes raw pydantic output — not rider copy
    case 'GEOCODE_FAILED':
      return ERROR_GEOCODE // backend appends the raw Routes API error — not rider copy
    case 'NO_ROUTE':
      return err.message // fixed rider-written backend copy
    case 'UPSTREAM':
      // With the envelope this is the backend's own 502 UPSTREAM. Without it,
      // 'UPSTREAM' is api.ts's default for unenveloped errors: a proxy answering
      // for a dead backend (Vite dev proxy → 502, empty body) vs. the backend
      // itself crashing (FastAPI → 500 "Internal Server Error").
      if (err.enveloped) return ERROR_UPSTREAM
      return err.status !== 502 && err.bodyText.trim() ? ERROR_BACKEND : ERROR_NETWORK
    default:
      return ERROR_UPSTREAM
  }
}

export default function App() {
  if (!API_KEY) return <SetupCard />
  return <Planner apiKey={API_KEY} />
}

function Planner({ apiKey }: { apiKey: string }) {
  const [origin, setOrigin] = useState<PlaceSelection | null>(null)
  const [destination, setDestination] = useState<PlaceSelection | null>(null)
  const [budget, setBudget] = useState(15)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [plan, setPlan] = useState<PlanResponse | null>(null)
  const [planKey, setPlanKey] = useState(0)
  const [hasPlanned, setHasPlanned] = useState(false)

  const canPlan = origin !== null && destination !== null && !loading

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!origin || !destination || loading) return
    setLoading(true)
    setError(null)
    try {
      const result = await planRoute({
        origin: { place_id: origin.placeId },
        destination: { place_id: destination.placeId },
        max_extra_minutes: budget,
      })
      setPlan(result)
      setPlanKey((key) => key + 1) // re-key TradePanel so the reveal animation replays
    } catch (err) {
      setError(describeError(err))
    } finally {
      setLoading(false)
      setHasPlanned(true)
    }
  }

  return (
    <APIProvider apiKey={apiKey}>
      <div className="app">
        <MapView plan={plan} />
        <aside className="panel">
          <header>
            <h1 className="wordmark">
              ihate<span className="strike">highways</span>
            </h1>
            <p className="tagline">Trade a few minutes for real roads.</p>
          </header>

          <form className="plan-form" onSubmit={handleSubmit}>
            <PlaceField label="From" value={origin} onChange={setOrigin} />
            <PlaceField label="To" value={destination} onChange={setDestination} />
            <BudgetSlider value={budget} onChange={setBudget} />
            <button type="submit" className="btn-primary" disabled={!canPlan}>
              {loading ? 'Finding real roads…' : 'Plan ride'}
            </button>
          </form>

          <div className="results" aria-live="polite">
            {error && <div className="error-card">{error}</div>}
            {!hasPlanned && !plan && (
              <p className="empty-line">
                Pick a start and an end. We&rsquo;ll get you off the highway.
              </p>
            )}
            {plan && <TradePanel key={planKey} plan={plan} />}
          </div>
        </aside>
      </div>
    </APIProvider>
  )
}
