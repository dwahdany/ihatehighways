import { Map } from '@vis.gl/react-google-maps'
import type { PlanResponse } from '../api'
import RouteLayer from './RouteLayer'

interface MapViewProps {
  plan: PlanResponse | null
}

export default function MapView({ plan }: MapViewProps) {
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
        {plan && <RouteLayer plan={plan} />}
      </Map>
    </div>
  )
}
