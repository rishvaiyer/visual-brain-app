import SwiftUI
import WebKit

struct PrivateBoardView: UIViewRepresentable {
    let client: LibraryClient
    let url: URL

    func makeCoordinator() -> Coordinator { Coordinator(origin: client.origin) }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.isOpaque = false
        Task { @MainActor in
            for cookie in client.sessionCookies {
                await view.configuration.websiteDataStore.httpCookieStore.setCookie(cookie)
            }
            view.load(URLRequest(url: url))
        }
        return view
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate {
        let origin: URL
        init(origin: URL) { self.origin = origin }
        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                     decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
            guard let target = navigationAction.request.url, LibraryClient.sameOrigin(target, origin) else { decisionHandler(.cancel); return }
            decisionHandler(.allow)
        }
    }
}
