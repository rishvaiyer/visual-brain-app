import SwiftUI

@main
struct VisualBrainApp: App {
    @StateObject private var model = BrainModel()
    var body: some Scene {
        WindowGroup {
            BrainView(model: model)
                .onOpenURL { url in Task { await model.handleCallback(url) } }
        }
    }
}
