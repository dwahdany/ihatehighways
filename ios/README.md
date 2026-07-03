# ihatehighways — iOS

SwiftUI app (iOS 17+, bundle id `eu.wahdany.ihatehighways`) on the same backend
contract as the web frontend: scout the cuts, compose the ride client-side, then
hand the composed route to the Google Navigation SDK via a route token.

## Open the project

The Xcode project is generated from `project.yml` with [xcodegen](https://github.com/yonaskolb/XcodeGen):

```sh
cd ios
cp Secrets.example.xcconfig Secrets.xcconfig   # then fill in MAPS_API_KEY
nix-shell -p xcodegen --run 'xcodegen generate'   # or: brew install xcodegen
open ihatehighways.xcodeproj
```

First build fetches the [Navigation SDK for iOS](https://github.com/googlemaps/ios-navigation-sdk)
via Swift Package Manager (large binary — give it a few minutes).

## Where the key lives

`ios/Secrets.xcconfig` (gitignored) defines `MAPS_API_KEY`. project.yml wires it
into Info.plist via build-setting substitution (`MAPS_API_KEY = $(MAPS_API_KEY)`),
and `AppConfig.mapsAPIKey` reads it at launch for `GMSServices.provideAPIKey`.
The key needs the Navigation SDK enabled in Google Maps Platform.

## Pointing at a local backend

Production base URL is `https://ihatehighways.wahdany.eu`. Debug builds check,
in order:

1. `IHH_BASE_URL` environment variable — set it in the Xcode scheme
   (Product → Scheme → Edit Scheme → Run → Arguments → Environment Variables),
   e.g. `http://192.168.1.10:8000`.
2. `IHH_BASE_URL` in UserDefaults, e.g. on a booted simulator:
   `xcrun simctl spawn booted defaults write eu.wahdany.ihatehighways IHH_BASE_URL http://localhost:8000`

Release builds always use production.

## Layout

- `Sources/Models.swift` — Codable mirrors of `docs/api.md` (snake_case wire format)
- `Sources/APIClient.swift` — `/api/scout`, `/api/ride-token`, `{detail: {code, message}}` errors
- `Sources/Compose.swift` — port of `frontend/src/lib/compose.ts` (worth, presets, totals)
- `Sources/PlanView.swift` — text fields, map with skeleton + tappable cuts, cut list, presets
- `Sources/RideView.swift` — ride token → `GMSNavigationWaypoint`s → `setDestinations(_:routeToken:)` → guidance
- `Sources/Polyline.swift` — Google encoded-polyline decoder (precision 5)
