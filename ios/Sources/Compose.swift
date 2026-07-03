import Foundation

/// Client-side ride composition, a direct port of frontend/src/lib/compose.ts:
/// a scout result + a set of selected cut ids fully determines the ride. Totals
/// are additive (fastest + Σ selected extras), so toggling cuts is instant.

struct ComposedSegment {
    let kind: SegmentKind
    var durationS: Double
}

struct ComposedRide {
    let durationS: Double
    let extraDurationS: Double
    let distanceM: Double
    let highwayDurationS: Double
    let highwayDistanceM: Double
    let segments: [ComposedSegment]
}

// Note: cut ids are unique by contract (c0..cN); uniquingKeysWith is belt-and-braces
// so a malformed response can't crash the app.
func composeRide(scout: ScoutResponse, selected: Set<String>) -> ComposedRide {
    let cutsById = Dictionary(scout.cuts.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
    var segments: [ComposedSegment] = []
    var distance = 0.0
    var highwayS = 0.0
    var highwayM = 0.0

    for part in scout.skeleton {
        if let cutId = part.cutId, selected.contains(cutId), let cut = cutsById[cutId] {
            segments.append(ComposedSegment(kind: .detour, durationS: cut.detourDurationS))
            distance += cut.detourDistanceM
            // Residual highway inside the detour (soft avoidance): baseline − avoided.
            highwayS += max(part.durationS - cut.avoidedHighwayS, 0)
            highwayM += max(part.distanceM - cut.avoidedHighwayM, 0)
        } else {
            segments.append(ComposedSegment(kind: part.kind, durationS: part.durationS))
            distance += part.distanceM
            if part.kind == .highway {
                highwayS += part.durationS
                highwayM += part.distanceM
            }
        }
    }

    // Merge adjacent same-kind segments so the ribbon reads clean.
    var merged: [ComposedSegment] = []
    for segment in segments {
        if let last = merged.last, last.kind == segment.kind {
            merged[merged.count - 1].durationS += segment.durationS
        } else {
            merged.append(segment)
        }
    }

    let extra = scout.cuts
        .filter { selected.contains($0.id) }
        .reduce(0.0) { $0 + $1.extraDurationS }

    return ComposedRide(
        // Skeleton part durations are rounded ints; anchor totals on the exact
        // fastest duration + extras so numbers stay consistent with the cut list.
        durationS: scout.fastest.durationS + extra,
        extraDurationS: extra,
        distanceM: distance,
        highwayDurationS: highwayS,
        highwayDistanceM: highwayM,
        segments: merged
    )
}

// Mirrors the backend's worth gate constants (config.py: curvy_boost, curviness_cap).
private let curvyBoost = 2.0
private let curvinessCap = 1.7

/// Fun-per-second-paid: highway time shed, boosted by curviness, per extra second.
/// A 1.5×-curvy sweep justifies more time loss than an arrow-straight B-road.
func cutWorth(_ cut: Cut) -> Double {
    if cut.extraDurationS <= 0 { return .infinity }
    let boost = 1 + curvyBoost * (min(cut.curviness, curvinessCap) - 1)
    return (cut.avoidedHighwayS * boost) / cut.extraDurationS
}

enum Preset: String, CaseIterable, Identifiable {
    case fastest
    case value
    case country

    var id: String { rawValue }

    var label: String {
        switch self {
        case .fastest: return "Fastest"
        case .value: return "Good deals"
        case .country: return "Country"
        }
    }
}

private let goodDealMinWorth = 1.0
// Default-selection sanity budget: individually-good cuts must not quietly turn a
// 7 h ride into a 13 h one. The rider can always toggle more on.
private let goodDealBudgetFraction = 0.15
private let goodDealMinBudgetS = 15.0 * 60

func presetSelection(scout: ScoutResponse, preset: Preset) -> Set<String> {
    switch preset {
    case .fastest:
        // Free cuts still belong in "fastest": the highway is jammed there.
        return Set(scout.cuts.filter { $0.extraDurationS <= 0 }.map(\.id))
    case .value:
        // Best trades first, within ~15% of the fastest time in total.
        let budget = max(scout.fastest.durationS * goodDealBudgetFraction, goodDealMinBudgetS)
        var picked = Set<String>()
        var spent = 0.0
        // Tie-break on id: Swift's sort isn't stable, and JS Array.sort is — keep the
        // web and app picking identical cuts when several tie on worth (e.g. free ones).
        let ranked = scout.cuts.sorted {
            let (a, b) = (cutWorth($0), cutWorth($1))
            return a == b ? $0.id < $1.id : a > b
        }
        for cut in ranked {
            if cutWorth(cut) < goodDealMinWorth { break }
            let cost = max(cut.extraDurationS, 0)
            if spent + cost > budget { continue } // a cheaper good deal may still fit
            picked.insert(cut.id)
            spent += cost
        }
        return picked
    case .country:
        return Set(scout.cuts.map(\.id))
    }
}
