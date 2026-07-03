import SwiftUI
import UIKit

extension UIColor {
    convenience init(hex: UInt32) {
        self.init(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}

/// Signage palette on asphalt, matching the web frontend.
enum Theme {
    static let asphalt = UIColor(hex: 0x16181B)
    static let highway = UIColor(hex: 0x5B8DD9) // signage blue: the enemy
    static let kept = UIColor(hex: 0xC9A227) // muted yellow: non-highway skeleton
    static let cut = UIColor(hex: 0xF7C948) // signage yellow: the fun part

    static let asphaltColor = Color(uiColor: asphalt)
    static let highwayColor = Color(uiColor: highway)
    static let keptColor = Color(uiColor: kept)
    static let cutColor = Color(uiColor: cut)
    static let panel = Color(uiColor: UIColor(hex: 0x1F2226))
}

func formatMinutes(_ seconds: Double) -> String {
    "\(Int((seconds / 60).rounded())) min"
}
