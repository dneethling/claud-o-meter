import ActivityKit
import SwiftUI
import WidgetKit

// The Live Activity: a Lock Screen card plus the Dynamic Island in its compact,
// minimal, and expanded forms. ActivityKit drives all of these from the same
// `context.state`, which the relay refreshes over APNs.
struct ClaudeUsageLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: ClaudeUsageAttributes.self) { context in
            LockScreenView(state: context.state)
                .activityBackgroundTint(Color.black.opacity(0.35))
                .activitySystemActionForegroundColor(.primary)
        } dynamicIsland: { context in
            let state = context.state
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    LabeledRing(title: "Session", pct: state.session_pct,
                                colorName: state.session_color, reset: state.session_reset)
                        .padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    LabeledRing(title: "Weekly", pct: state.weekly_pct,
                                colorName: state.weekly_color, reset: state.weekly_reset)
                        .padding(.trailing, 4)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    if state.isHealthy {
                        if state.on_credits, let credits = state.credits {
                            Label("On credits · \(credits.used) of \(credits.limit)",
                                  systemImage: "creditcard.fill")
                                .font(.caption2)
                                .foregroundStyle(MeterColor.named("orange"))
                        } else if !state.models.isEmpty {
                            HStack(spacing: 10) {
                                ForEach(state.models) { m in
                                    HStack(spacing: 4) {
                                        Circle().fill(MeterColor.named(m.color)).frame(width: 6, height: 6)
                                        Text("\(m.name) \(m.pct)%")
                                            .font(.caption2.monospacedDigit())
                                            .foregroundStyle(.secondary)
                                    }
                                }
                            }
                        }
                    } else {
                        StatusBadge(status: state.status, note: state.note)
                    }
                }
            } compactLeading: {
                CompactPct(tag: "S", pct: state.session_pct, colorName: state.session_color)
            } compactTrailing: {
                CompactPct(tag: "W", pct: state.weekly_pct, colorName: state.weekly_color)
            } minimal: {
                MinimalMeter(state: state)
            }
            .keylineTint(MeterColor.named(state.headlineColorName))
            .widgetURL(URL(string: "claudemeter://open"))
        }
    }
}
