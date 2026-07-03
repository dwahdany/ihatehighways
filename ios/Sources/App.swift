import SwiftUI
import GoogleMaps

enum AppConfig {
    /// Injected into Info.plist from Secrets.xcconfig via build-setting substitution.
    static var mapsAPIKey: String {
        (Bundle.main.object(forInfoDictionaryKey: "MAPS_API_KEY") as? String) ?? ""
    }
}

@main
struct IHateHighwaysApp: App {
    init() {
        GMSServices.provideAPIKey(AppConfig.mapsAPIKey)
    }

    var body: some Scene {
        WindowGroup {
            PlanView()
                .preferredColorScheme(.dark)
        }
    }
}
