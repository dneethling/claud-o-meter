import SwiftUI

// A circular percentage ring. Kept dependency-free (plain Shapes) so it renders
// identically in the Dynamic Island, on the Lock Screen, and in previews.
struct RingMeter: View {
    let pct: Int?
    let colorName: String
    var lineWidth: CGFloat = 5
    var showLabel: Bool = true

    var body: some View {
        ZStack {
            Circle()
                .stroke(Color.secondary.opacity(0.25), lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: pct.pctFraction)
                .stroke(MeterColor.named(colorName),
                        style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                .rotationEffect(.degrees(-90))
            if showLabel {
                Text(pct.pctLabel)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .minimumScaleFactor(0.6)
                    .foregroundStyle(.primary)
            }
        }
    }
}

// A labelled ring with a caption line ("Session · resets in 2h") for the
// expanded island and the Lock Screen.
struct LabeledRing: View {
    let title: String
    let pct: Int?
    let colorName: String
    let reset: String

    var body: some View {
        VStack(spacing: 4) {
            RingMeter(pct: pct, colorName: colorName)
                .frame(width: 44, height: 44)
            Text(title)
                .font(.caption2).foregroundStyle(.secondary)
            if !reset.isEmpty {
                Text(reset)
                    .font(.system(size: 10)).foregroundStyle(.tertiary)
                    .lineLimit(1).minimumScaleFactor(0.7)
            }
        }
    }
}

// Compact leading/trailing content: a small coloured percentage with a one-letter
// tag so the two numbers are distinguishable in the tiny pill.
struct CompactPct: View {
    let tag: String
    let pct: Int?
    let colorName: String

    var body: some View {
        HStack(spacing: 2) {
            Text(tag)
                .font(.system(size: 11, weight: .bold, design: .rounded))
                .foregroundStyle(.secondary)
            Text(pct.pctLabel)
                .font(.system(size: 14, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(MeterColor.named(colorName))
        }
    }
}

// The single view that fits the Dynamic Island's "minimal" slot: whichever metric
// is closest to its limit right now (a small unlabelled ring).
struct MinimalMeter: View {
    let state: ClaudeUsageAttributes.ContentState

    var body: some View {
        let showWeekly = (state.weekly_pct ?? 0) >= (state.session_pct ?? 0)
        return RingMeter(pct: showWeekly ? state.weekly_pct : state.session_pct,
                         colorName: showWeekly ? state.weekly_color : state.session_color,
                         lineWidth: 3, showLabel: false)
            .frame(width: 18, height: 18)
    }
}

// Small badge used when the relay reports a problem instead of usage.
struct StatusBadge: View {
    let status: String
    let note: String

    private var glyph: String {
        switch status {
        case "reauth": return "person.badge.key"
        case "error":  return "exclamationmark.triangle"
        default:       return "hourglass"
        }
    }
    private var tint: Color { status == "error" ? MeterColor.named("red") : MeterColor.named("orange") }

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: glyph).foregroundStyle(tint)
            Text(note.isEmpty ? statusText : note)
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(2).minimumScaleFactor(0.7)
        }
    }
    private var statusText: String {
        switch status {
        case "reauth": return "Sign in to claude.ai on the relay machine"
        case "error":  return "Usage fetch failed"
        default:       return "Starting…"
        }
    }
}

// The Lock Screen / notification-banner presentation of the activity.
struct LockScreenView: View {
    let state: ClaudeUsageAttributes.ContentState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Claude usage", systemImage: "gauge.with.dots.needle.33percent")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if state.updated_epoch > 0 {
                    Text(Date(timeIntervalSince1970: TimeInterval(state.updated_epoch)),
                         style: .relative)
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }

            if state.isHealthy {
                HStack(alignment: .top, spacing: 18) {
                    LabeledRing(title: "Session", pct: state.session_pct,
                                colorName: state.session_color, reset: state.session_reset)
                    LabeledRing(title: "Weekly", pct: state.weekly_pct,
                                colorName: state.weekly_color, reset: state.weekly_reset)
                    if !state.models.isEmpty {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(state.models) { m in
                                HStack(spacing: 6) {
                                    Circle().fill(MeterColor.named(m.color)).frame(width: 7, height: 7)
                                    Text(m.name).font(.caption2)
                                    Spacer(minLength: 4)
                                    Text("\(m.pct)%").font(.caption2.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                if state.on_credits, let credits = state.credits {
                    Text("On credits · \(credits.used) of \(credits.limit)")
                        .font(.caption2).foregroundStyle(MeterColor.named("orange"))
                }
            } else {
                StatusBadge(status: state.status, note: state.note)
            }
        }
        .padding()
    }
}
