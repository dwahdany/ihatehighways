import Foundation

// Codable mirrors of docs/api.md. Wire format is snake_case; the shared
// decoder/encoder in APIClient uses .convertFromSnakeCase/.convertToSnakeCase,
// so property names here are the camelCase twins of the documented keys.

struct LatLng: Codable, Hashable {
    let lat: Double
    let lng: Double
}

enum SegmentKind: String, Codable {
    case kept
    case highway
    case detour
}

/// `fastest` object shared by /api/plan and /api/scout.
struct FastestRoute: Codable {
    let encodedPolyline: String
    let durationS: Double
    let staticDurationS: Double
    let distanceM: Double
    let highwayDistanceM: Double
    let highwayDurationS: Double
}

/// One part of the scout skeleton; polylines concatenate into the fastest route.
/// Every non-nil `cutId` matches exactly one cut (kind is always "highway" then).
struct SkeletonPart: Codable {
    let kind: SegmentKind
    let encodedPolyline: String
    let durationS: Double
    let distanceM: Double
    let cutId: String?
}

/// A viable highway cut: the country-road replacement for one skeleton part.
struct Cut: Codable, Identifiable {
    let id: String
    let road: String
    let entry: LatLng
    let mid: LatLng
    let exit: LatLng
    let encodedPolyline: String
    let detourDurationS: Double
    let detourDistanceM: Double
    let extraDurationS: Double
    let avoidedHighwayS: Double
    let avoidedHighwayM: Double
    let curviness: Double
}

/// `POST /api/scout` response.
struct ScoutResponse: Codable {
    let origin: LatLng
    let destination: LatLng
    let fastest: FastestRoute
    let skeleton: [SkeletonPart]
    let cuts: [Cut]
}

// MARK: - /api/ride-token

struct RideTokenRequest: Codable {
    struct CutRef: Codable {
        let entry: LatLng
        let mid: LatLng
        let exit: LatLng
        let encodedPolyline: String
    }

    let origin: LatLng
    let destination: LatLng
    let cuts: [CutRef]
}

struct RideTokenResponse: Codable {
    let routeToken: String
    let encodedPolyline: String
    let durationS: Double
    let distanceM: Double
    let waypoints: [LatLng]
    /// Parallel to the request's cuts: false means Google may straighten that cut.
    let cutsFollowed: [Bool]
}
