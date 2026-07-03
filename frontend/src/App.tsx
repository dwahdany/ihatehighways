import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { APIProvider } from '@vis.gl/react-google-maps'
import { ApiError, scoutRouteStream } from './api'
import type { ScoutResponse } from './api'
import { buildHandoff, composeRide, presetSelection } from './lib/compose'
import { formatSignedMinutes } from './lib/format'
import { EMPTY_PROGRESS, applyEvent } from './lib/progress'
import type { ScoutProgress } from './lib/progress'
import MapView from './components/MapView'
import PlaceField from './components/PlaceField'
import type { PlaceSelection } from './components/PlaceField'
import CutList from './components/CutList'
import ScoutLoader from './components/ScoutLoader'
import TradePanel from './components/TradePanel'
import SetupCard from './components/SetupCard'

const NOTHING_SELECTED: ReadonlySet<string> = new Set()

const API_KEY: string | undefined = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

const ERROR_UPSTREAM = "Google couldn't route that. Try different points."
const ERROR_NETWORK = "Backend isn't reachable. Is it running on :8000?"
const ERROR_BACKEND = 'The backend hit an unexpected error. Try again in a moment.'
const ERROR_INVALID = 'The backend rejected that request. Adjust start or end and try again.'
const ERROR_GEOCODE = "Couldn't pin down one of those places. Pick start and end from the suggestions."

/** Map a scout failure to rider-facing copy (error codes per docs/api.md). */
function describeError(err: unknown): string {
  if (!(err instanceof ApiError)) return ERROR_NETWORK // fetch itself failed
  switch (err.code) {
    case 'INVALID_INPUT':
      return ERROR_INVALID // backend echoes raw pydantic output — not rider copy
    case 'GEOCODE_FAILED':
      return ERROR_GEOCODE // backend appends the raw Routes API error — not rider copy
    case 'NO_ROUTE':
    case 'RATE_LIMITED':
    case 'DAILY_CAP':
      return err.message // fixed rider-written backend copy
    case 'UPSTREAM':
      // With the envelope this is the backend's own 502 UPSTREAM. Without it,
      // 'UPSTREAM' is api.ts's default for unenveloped errors: a proxy answering
      // for a dead backend (502, empty body) vs. the backend itself crashing
      // (FastAPI → 500 "Internal Server Error").
      if (err.enveloped) return ERROR_UPSTREAM
      return err.status !== 502 && err.bodyText.trim() ? ERROR_BACKEND : ERROR_NETWORK
    default:
      return ERROR_UPSTREAM
  }
}

const PRESETS = [
  { key: 'fastest', label: 'Fastest' },
  { key: 'value', label: 'Good deals' },
  { key: 'country', label: 'Full country' },
] as const

type PresetKey = (typeof PRESETS)[number]['key']

export default function App() {
  if (!API_KEY) return <SetupCard />
  return <Planner apiKey={API_KEY} />
}

function Planner({ apiKey }: { apiKey: string }) {
  const [origin, setOrigin] = useState<PlaceSelection | null>(null)
  const [destination, setDestination] = useState<PlaceSelection | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [scout, setScout] = useState<ScoutResponse | null>(null)
  const [progress, setProgress] = useState<ScoutProgress | null>(null)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [scoutKey, setScoutKey] = useState(0)
  const [hasScouted, setHasScouted] = useState(false)

  const canScout = origin !== null && destination !== null && !loading

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!origin || !destination || loading) return
    setLoading(true)
    setError(null)
    setProgress(EMPTY_PROGRESS)
    try {
      await scoutRouteStream(
        {
          origin: { place_id: origin.placeId },
          destination: { place_id: destination.placeId },
        },
        (streamEvent) => {
          if (streamEvent.type === 'done') {
            setScout(streamEvent.scout)
            setSelected(presetSelection(streamEvent.scout, 'value'))
            setScoutKey((key) => key + 1) // re-key TradePanel so the reveal replays
          } else if (streamEvent.type === 'error') {
            setError(
              describeError(
                new ApiError(streamEvent.status, streamEvent.code, streamEvent.message, true),
              ),
            )
            // Don't snap back to the previous ride under a fresh error — the rider
            // just watched the new route being drawn; map and panel must agree.
            setScout(null)
            setSelected(new Set())
          } else {
            setProgress((current) => applyEvent(current ?? EMPTY_PROGRESS, streamEvent))
          }
        },
      )
    } catch (err) {
      setError(describeError(err))
      setScout(null)
      setSelected(new Set())
    } finally {
      setLoading(false)
      setProgress(null)
      setHasScouted(true)
    }
  }

  function toggleCut(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const ride = useMemo(() => (scout ? composeRide(scout, selected) : null), [scout, selected])
  // The fastest route in ride order, so the ribbon's two bars share one legend.
  const fastestSegments = useMemo(
    () => (scout ? composeRide(scout, NOTHING_SELECTED).segments : []),
    [scout],
  )
  const handoff = useMemo(
    () => (scout ? buildHandoff(scout, selected) : null),
    [scout, selected],
  )
  // What each preset would cost — shown on the buttons so a tap is never a surprise.
  const presetExtras = useMemo(() => {
    if (!scout) return null
    const entries = PRESETS.map((preset) => [
      preset.key,
      composeRide(scout, presetSelection(scout, preset.key)).extra_duration_s,
    ])
    return Object.fromEntries(entries) as Record<PresetKey, number>
  }, [scout])

  const activePreset = useMemo<PresetKey | null>(() => {
    if (!scout) return null
    for (const preset of PRESETS) {
      const wanted = presetSelection(scout, preset.key)
      if (wanted.size === selected.size && [...wanted].every((id) => selected.has(id))) {
        return preset.key
      }
    }
    return null
  }, [scout, selected])

  return (
    <APIProvider apiKey={apiKey}>
      <div className="app">
        <MapView
          scout={scout}
          selected={selected}
          onToggle={toggleCut}
          progress={loading ? progress : null}
        />
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
            <button type="submit" className="btn-primary" disabled={!canScout}>
              {loading ? 'Scouting cuts…' : 'Scout the ride'}
            </button>
          </form>

          <div className="results" aria-live="polite">
            {loading && <ScoutLoader progress={progress ?? EMPTY_PROGRESS} />}
            {!loading && error && <div className="error-card">{error}</div>}
            {!loading && !hasScouted && !scout && (
              <p className="empty-line">
                Pick a start and an end. We&rsquo;ll find the highway worth cutting.
              </p>
            )}
            {!loading && scout && ride && (
              <>
                <TradePanel
                  key={scoutKey}
                  fastest={scout.fastest}
                  fastestSegments={fastestSegments}
                  ride={ride}
                  handoff={handoff!}
                />
                {scout.cuts.length > 0 ? (
                  <>
                    <div className="presets" role="group" aria-label="Quick picks">
                      {PRESETS.map((preset) => (
                        <button
                          key={preset.key}
                          type="button"
                          className={`preset-btn${activePreset === preset.key ? ' preset-active' : ''}`}
                          aria-pressed={activePreset === preset.key}
                          onClick={() => setSelected(presetSelection(scout, preset.key))}
                        >
                          <span>{preset.label}</span>
                          {presetExtras && (
                            <span className="preset-extra">
                              {formatSignedMinutes(presetExtras[preset.key])} min
                            </span>
                          )}
                        </button>
                      ))}
                    </div>
                    <CutList cuts={scout.cuts} selected={selected} onToggle={toggleCut} />
                  </>
                ) : scout.fastest.highway_duration_s === 0 ? (
                  <p className="note">No highway on the fastest route — enjoy.</p>
                ) : (
                  <p className="note">
                    No worthwhile cuts on this route — the country roads nearby don&rsquo;t
                    pay their way.
                  </p>
                )}
              </>
            )}
          </div>

          <p className="attribution">
            Routing &copy; Google &middot; corridor data &copy; OpenStreetMap contributors
          </p>
        </aside>
      </div>
    </APIProvider>
  )
}
