import Foundation

/// Backend error envelope: `{"detail": {"code": ..., "message": ...}}`.
struct ApiError: Error, LocalizedError {
    let code: String
    let message: String
    let status: Int

    var errorDescription: String? { message }
}

private struct ErrorEnvelope: Decodable {
    struct Detail: Decodable {
        let code: String
        let message: String
    }

    let detail: Detail
}

final class APIClient {
    /// Production backend; Debug builds can point elsewhere via the IHH_BASE_URL
    /// environment variable (Xcode scheme) or UserDefaults key "IHH_BASE_URL"
    /// (e.g. `xcrun simctl spawn booted defaults write eu.wahdany.ihatehighways IHH_BASE_URL http://192.168.1.10:8000`).
    static var baseURL: URL {
        #if DEBUG
        let override = ProcessInfo.processInfo.environment["IHH_BASE_URL"]
            ?? UserDefaults.standard.string(forKey: "IHH_BASE_URL")
        if let override, let url = URL(string: override) {
            return url
        }
        #endif
        return URL(string: "https://ihatehighways.wahdany.eu")!
    }

    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init() {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 120 // scouting probes take a while
        session = URLSession(configuration: configuration)

        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
    }

    /// Plain-text waypoints, no Places SDK in v1: the backend geocodes `address`.
    private struct ScoutRequest: Encodable {
        struct Waypoint: Encodable {
            let address: String
        }

        let origin: Waypoint
        let destination: Waypoint
    }

    func scout(origin: String, destination: String) async throws -> ScoutResponse {
        try await post(
            path: "api/scout",
            body: ScoutRequest(origin: .init(address: origin), destination: .init(address: destination))
        )
    }

    func rideToken(_ request: RideTokenRequest) async throws -> RideTokenResponse {
        try await post(path: "api/ride-token", body: request)
    }

    private func post<Body: Encodable, Response: Decodable>(path: String, body: Body) async throws -> Response {
        var request = URLRequest(url: Self.baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try encoder.encode(body)

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(http.statusCode) else {
            if let envelope = try? decoder.decode(ErrorEnvelope.self, from: data) {
                throw ApiError(code: envelope.detail.code, message: envelope.detail.message, status: http.statusCode)
            }
            throw ApiError(
                code: "HTTP_\(http.statusCode)",
                message: "The backend answered \(http.statusCode) without a readable error.",
                status: http.statusCode
            )
        }
        return try decoder.decode(Response.self, from: data)
    }
}
