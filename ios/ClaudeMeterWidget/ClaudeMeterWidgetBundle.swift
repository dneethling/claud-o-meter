import SwiftUI
import WidgetKit

// The widget extension's entry point. Only the Live Activity lives here for now;
// a Home/Lock Screen WidgetKit widget could be added to this bundle later.
@main
struct ClaudeMeterWidgetBundle: WidgetBundle {
    var body: some Widget {
        ClaudeUsageLiveActivity()
    }
}
