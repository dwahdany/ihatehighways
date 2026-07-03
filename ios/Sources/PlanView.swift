import GoogleMaps
import SwiftUI

@MainActor
final class PlanModel: ObservableObject {
    @Published var origin = ""
    @Published var destination = ""
    @Published var scout: ScoutResponse?
    @Published var selected: Set<String> = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var showRide = false

    private let api = APIClient()

    var composed: ComposedRide? {
        scout.map { composeRide(scout: $0, selected: selected) }
    }

    /// Cuts ranked by worth, best trades first (same ranking as the web cut list).
    var rankedCuts: [Cut] {
        (scout?.cuts ?? []).sorted { cutWorth($0) > cutWorth($1) }
    }

    func runScout() async {
        let origin = origin.trimmingCharacters(in: .whitespacesAndNewlines)
        let destination = destination.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !origin.isEmpty, !destination.isEmpty else { return }
        isLoading = true
        errorMessage = nil
        do {
            let response = try await api.scout(origin: origin, destination: destination)
            scout = response
            selected = presetSelection(scout: response, preset: .value)
        } catch let error as ApiError {
            errorMessage = error.message
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func toggle(_ cutId: String) {
        if selected.contains(cutId) {
            selected.remove(cutId)
        } else {
            selected.insert(cutId)
        }
    }

    func apply(_ preset: Preset) {
        guard let scout else { return }
        selected = presetSelection(scout: scout, preset: preset)
    }

    func extraMinutes(for preset: Preset) -> Double {
        guard let scout else { return 0 }
        let ids = presetSelection(scout: scout, preset: preset)
        return scout.cuts.filter { ids.contains($0.id) }.reduce(0.0) { $0 + $1.extraDurationS }
    }
}

struct PlanView: View {
    @StateObject private var model = PlanModel()
    @FocusState private var focusedField: Bool

    var body: some View {
        ZStack {
            Theme.asphaltColor.ignoresSafeArea()
            VStack(spacing: 0) {
                searchBar
                    .padding(.horizontal)
                    .padding(.bottom, 10)

                PlanMapView(scout: model.scout, selected: $model.selected)
                    .frame(maxHeight: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .padding(.horizontal)

                if let error = model.errorMessage {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(.horizontal)
                        .padding(.top, 8)
                }

                if let scout = model.scout, let composed = model.composed {
                    tradeHeader(scout: scout, composed: composed)
                    presetRow
                    cutList
                    rideButton
                } else {
                    Spacer().frame(height: 12)
                }
            }
        }
        .fullScreenCover(isPresented: $model.showRide) {
            if let scout = model.scout {
                RideView(scout: scout, selected: model.selected)
            }
        }
    }

    private var searchBar: some View {
        VStack(spacing: 8) {
            TextField("From", text: $model.origin)
                .textFieldStyle(.roundedBorder)
                .autocorrectionDisabled()
            HStack(spacing: 8) {
                TextField("To", text: $model.destination)
                    .textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                    .focused($focusedField)
                Button {
                    focusedField = false
                    Task { await model.runScout() }
                } label: {
                    if model.isLoading {
                        ProgressView().tint(.black)
                    } else {
                        Text("Scout").fontWeight(.semibold)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.cutColor)
                .foregroundStyle(.black)
                .disabled(model.isLoading)
            }
        }
        .padding(.top, 8)
    }

    /// "+X min / −Y min highway" versus the fastest route.
    private func tradeHeader(scout: ScoutResponse, composed: ComposedRide) -> some View {
        HStack(spacing: 12) {
            Text("+\(formatMinutes(max(composed.extraDurationS, 0)))")
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(Theme.cutColor)
            Text("−\(formatMinutes(max(scout.fastest.highwayDurationS - composed.highwayDurationS, 0))) highway")
                .font(.title3.weight(.bold).monospacedDigit())
                .foregroundStyle(Theme.highwayColor)
            Spacer()
            Text(formatMinutes(composed.durationS) + " total")
                .font(.footnote.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal)
        .padding(.top, 12)
    }

    private var presetRow: some View {
        HStack(spacing: 8) {
            ForEach(Preset.allCases) { preset in
                Button {
                    model.apply(preset)
                } label: {
                    VStack(spacing: 2) {
                        Text(preset.label).font(.footnote.weight(.semibold))
                        Text("+\(formatMinutes(max(model.extraMinutes(for: preset), 0)))")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                }
                .buttonStyle(.bordered)
                .tint(Theme.keptColor)
            }
        }
        .padding(.horizontal)
        .padding(.top, 8)
    }

    private var cutList: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                ForEach(model.rankedCuts) { cut in
                    CutRow(
                        cut: cut,
                        isSelected: model.selected.contains(cut.id),
                        onToggle: { model.toggle(cut.id) }
                    )
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
        }
        .frame(maxHeight: 240)
    }

    private var rideButton: some View {
        Button {
            model.showRide = true
        } label: {
            Text("Ride")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
        }
        .buttonStyle(.borderedProminent)
        .tint(Theme.cutColor)
        .foregroundStyle(.black)
        .padding(.horizontal)
        .padding(.bottom, 10)
        .disabled(model.scout == nil)
    }
}

private struct CutRow: View {
    let cut: Cut
    let isSelected: Bool
    let onToggle: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(cut.road)
                    .font(.subheadline.weight(.semibold))
                HStack(spacing: 10) {
                    Text("−\(formatMinutes(cut.avoidedHighwayS)) highway")
                        .foregroundStyle(Theme.highwayColor)
                    Text(cut.extraDurationS <= 0 ? "free" : "+\(formatMinutes(cut.extraDurationS))")
                        .foregroundStyle(Theme.cutColor)
                    Text(String(format: "%.2f× curvy", cut.curviness))
                        .foregroundStyle(.secondary)
                }
                .font(.caption.monospacedDigit())
            }
            Spacer()
            Toggle("", isOn: Binding(get: { isSelected }, set: { _ in onToggle() }))
                .labelsHidden()
                .tint(Theme.cutColor)
        }
        .padding(10)
        .background(Theme.panel, in: RoundedRectangle(cornerRadius: 10))
        .opacity(isSelected ? 1 : 0.65)
    }
}

// MARK: - Map

/// GMSMapView wrapper: skeleton parts (highway blue, kept muted yellow) plus cut
/// polylines (signage yellow, ghosted when unselected; tap a cut to toggle it).
struct PlanMapView: UIViewRepresentable {
    let scout: ScoutResponse?
    @Binding var selected: Set<String>

    func makeCoordinator() -> Coordinator {
        Coordinator(self)
    }

    func makeUIView(context: Context) -> GMSMapView {
        let options = GMSMapViewOptions()
        options.camera = GMSCameraPosition.camera(withLatitude: 50.7, longitude: 7.1, zoom: 6)
        let mapView = GMSMapView(options: options)
        mapView.delegate = context.coordinator
        mapView.overrideUserInterfaceStyle = .dark
        return mapView
    }

    func updateUIView(_ mapView: GMSMapView, context: Context) {
        context.coordinator.parent = self
        context.coordinator.render(on: mapView)
    }

    final class Coordinator: NSObject, GMSMapViewDelegate {
        var parent: PlanMapView
        private var fittedPolyline: String?

        init(_ parent: PlanMapView) {
            self.parent = parent
        }

        func render(on mapView: GMSMapView) {
            mapView.clear()
            guard let scout = parent.scout else { return }

            for part in scout.skeleton {
                let line = GMSPolyline(path: path(from: part.encodedPolyline))
                line.strokeColor = part.kind == .highway ? Theme.highway : Theme.kept
                line.strokeWidth = part.kind == .highway ? 4 : 3
                line.zIndex = 1
                line.map = mapView
            }

            for cut in scout.cuts {
                let isSelected = parent.selected.contains(cut.id)
                let line = GMSPolyline(path: path(from: cut.encodedPolyline))
                line.strokeColor = isSelected ? Theme.cut : Theme.cut.withAlphaComponent(0.28)
                line.strokeWidth = isSelected ? 5 : 3
                line.zIndex = 2
                line.isTappable = true
                line.userData = cut.id
                line.map = mapView
            }

            // Fit the camera once per scout, not on every toggle.
            if fittedPolyline != scout.fastest.encodedPolyline {
                fittedPolyline = scout.fastest.encodedPolyline
                let path = path(from: scout.fastest.encodedPolyline)
                if path.count() > 0 {
                    let bounds = GMSCoordinateBounds(path: path)
                    mapView.animate(with: GMSCameraUpdate.fit(bounds, withPadding: 40))
                }
            }
        }

        func mapView(_ mapView: GMSMapView, didTap overlay: GMSOverlay) {
            guard let cutId = overlay.userData as? String else { return }
            if parent.selected.contains(cutId) {
                parent.selected.remove(cutId)
            } else {
                parent.selected.insert(cutId)
            }
        }

        private func path(from encoded: String) -> GMSMutablePath {
            let path = GMSMutablePath()
            for coordinate in Polyline.decode(encoded) {
                path.add(coordinate)
            }
            return path
        }
    }
}
