import CoreLocation

/// Google encoded polyline decoder, precision 5 (docs/api.md: all polylines use it).
enum Polyline {
    static func decode(_ encoded: String) -> [CLLocationCoordinate2D] {
        var coordinates: [CLLocationCoordinate2D] = []
        let bytes = Array(encoded.utf8)
        var index = 0
        var lat = 0
        var lng = 0

        func nextDelta() -> Int? {
            var result = 0
            var shift = 0
            while index < bytes.count {
                let chunk = Int(bytes[index]) - 63
                index += 1
                result |= (chunk & 0x1F) << shift
                shift += 5
                if chunk < 0x20 {
                    return (result & 1) != 0 ? ~(result >> 1) : result >> 1
                }
            }
            return nil // truncated input
        }

        while index < bytes.count {
            guard let dLat = nextDelta(), let dLng = nextDelta() else { break }
            lat += dLat
            lng += dLng
            coordinates.append(CLLocationCoordinate2D(latitude: Double(lat) / 1e5, longitude: Double(lng) / 1e5))
        }
        return coordinates
    }
}
