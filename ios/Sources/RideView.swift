import CoreLocation
import GoogleMaps
import GoogleNavigation
import SwiftUI

@MainActor
final class RideModel: ObservableObject {
    @Published var token: RideTokenResponse?
    @Published var errorMessage: String?
    @Published var isLoading = false
    @Published var isNavigating = false

    private let api = APIClient()
    private let locationManager = CLLocationManager()

    func fetchToken(scout: ScoutResponse, selected: Set<String>) async {
        guard token == nil, !isLoading else { return }
        isLoading = true
        errorMessage = nil

        // Cuts in route order (skeleton order), matching cuts_followed positions.
        let cutsById = Dictionary(scout.cuts.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        let orderedCuts: [RideTokenRequest.CutRef] = scout.skeleton.compactMap { part in
            guard let cutId = part.cutId, selected.contains(cutId), let cut = cutsById[cutId] else {
                return nil
            }
            return RideTokenRequest.CutRef(
                entry: cut.entry,
                mid: cut.mid,
                exit: cut.exit,
                encodedPolyline: cut.encodedPolyline
            )
        }
        let request = RideTokenRequest(origin: scout.origin, destination: scout.destination, cuts: orderedCuts)

        do {
            token = try await api.rideToken(request)
        } catch let error as ApiError {
            errorMessage = error.message
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    /// Terms dialog first (guidance stays off until accepted), then location, then nav.
    func startNavigation() {
        let options = GMSNavigationTermsAndConditionsOptions(companyName: "ihatehighways")
        GMSNavigationServices.showTermsAndConditionsDialogIfNeeded(with: options) { [weak self] termsAccepted in
            guard let self else { return }
            guard termsAccepted else {
                self.errorMessage = "Navigation needs the Google terms accepted — no terms, no turn-by-turn."
                return
            }
            self.locationManager.requestAlwaysAuthorization()
            self.isNavigating = true
        }
    }
}

struct RideView: View {
    let scout: ScoutResponse
    let selected: Set<String>

    @Environment(\.dismiss) private var dismiss
    @StateObject private var model = RideModel()

    var body: some View {
        ZStack(alignment: .topLeading) {
            Theme.asphaltColor.ignoresSafeArea()

            if model.isNavigating, let token = model.token {
                NavigationMapView(token: token)
                    .ignoresSafeArea()
            } else {
                preflight
            }

            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title)
                    .foregroundStyle(.white.opacity(0.8))
            }
            .padding()
        }
        .task {
            await model.fetchToken(scout: scout, selected: selected)
        }
    }

    private var preflight: some View {
        VStack(spacing: 18) {
            Spacer()
            Text("Ride")
                .font(.largeTitle.weight(.bold))

            if model.isLoading {
                ProgressView("Locking in your cuts…")
            } else if let token = model.token {
                let straightened = token.cutsFollowed.filter { !$0 }.count
                VStack(spacing: 8) {
                    Text("\(formatMinutes(token.durationS)) · \(Int((token.distanceM / 1000).rounded())) km")
                        .font(.title3.monospacedDigit())
                    if straightened == 0 {
                        Label("All cuts locked in", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(Theme.cutColor)
                    } else {
                        Label("\(straightened) cut\(straightened == 1 ? "" : "s") may be straightened", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }

                Button {
                    model.startNavigation()
                } label: {
                    Text("Start navigation")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.cutColor)
                .foregroundStyle(.black)
                .padding(.horizontal, 32)
            }

            if let error = model.errorMessage {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }
            Spacer()
        }
    }
}

/// Navigation SDK map: waypoints + route token from /api/ride-token, so guidance
/// follows the composed ride instead of re-planning its own fastest route.
struct NavigationMapView: UIViewRepresentable {
    let token: RideTokenResponse

    /// Cut pins are invisible checkpoints, not stops: with stopover waypoints (via
    /// pins are forbidden with route tokens) guidance halts at each arrival unless
    /// we roll straight on to the next destination.
    final class Coordinator: NSObject, GMSNavigatorListener {
        func navigator(_ navigator: GMSNavigator, didArriveAt waypoint: GMSNavigationWaypoint) {
            navigator.continueToNextDestination()
            navigator.isGuidanceActive = true
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> GMSMapView {
        let options = GMSMapViewOptions()
        options.camera = GMSCameraPosition.camera(
            withLatitude: token.waypoints.first?.lat ?? 0,
            longitude: token.waypoints.first?.lng ?? 0,
            zoom: 14
        )
        let mapView = GMSMapView(options: options)
        mapView.isNavigationEnabled = true
        mapView.cameraMode = .following
        mapView.travelMode = .driving
        mapView.navigator?.add(context.coordinator)

        let waypoints = token.waypoints.compactMap { point in
            GMSNavigationWaypoint(
                location: CLLocationCoordinate2D(latitude: point.lat, longitude: point.lng),
                title: ""
            )
        }

        mapView.navigator?.setDestinations(waypoints, routeToken: token.routeToken) { routeStatus in
            guard routeStatus == .OK else {
                NSLog("ihatehighways: setDestinations failed with GMSRouteStatus \(routeStatus.rawValue)")
                return
            }
            mapView.navigator?.isGuidanceActive = true
        }
        return mapView
    }

    func updateUIView(_ mapView: GMSMapView, context: Context) {}

    static func dismantleUIView(_ mapView: GMSMapView, coordinator: ()) {
        mapView.navigator?.isGuidanceActive = false
        mapView.navigator?.clearDestinations()
    }
}
