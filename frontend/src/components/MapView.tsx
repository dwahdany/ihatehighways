import { Map } from '@vis.gl/react-google-maps'
import type { ScoutResponse } from '../api'
import type { ScoutProgress } from '../lib/progress'
import ProgressLayer from './ProgressLayer'
import RouteLayer from './RouteLayer'

interface MapViewProps {
  scout: ScoutResponse | null
  selected: ReadonlySet<string>
  onToggle: (id: string) => void
  /** Live scouting progress; when set it replaces the result layer. */
  progress: ScoutProgress | null
}

export default function MapView({ scout, selected, onToggle, progress }: MapViewProps) {
  return (
    <div className="map">
      <Map
        mapId="DEMO_MAP_ID"
        colorScheme="DARK"
        defaultCenter={{ lat: 50.7, lng: 7.6 }}
        defaultZoom={8}
        gestureHandling="greedy"
        disableDefaultUI
      >
        {progress ? (
          <ProgressLayer progress={progress} />
        ) : (
          scout && <RouteLayer scout={scout} selected={selected} onToggle={onToggle} />
        )}
      </Map>
    </div>
  )
}
