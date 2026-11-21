import Foundation

struct LibraryStatus: Decodable, Sendable {
    let privateLibrary: Bool
    let voiceAvailable: Bool
    enum CodingKeys: String, CodingKey {
        case privateLibrary = "private_library"
        case voiceAvailable = "voice_available"
    }
}

struct SavedBoard: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
}

struct SavedMatch: Decodable, Identifiable, Sendable {
    let id: String
    let similarity: Double
    let boards: [String]
    let title: String?
    let boardNames: [SavedBoard]?
    let closeMatch: Bool?
    enum CodingKeys: String, CodingKey {
        case id, similarity, boards, title
        case boardNames = "board_names"
        case closeMatch = "close_match"
    }
    var displayTitle: String { title?.isEmpty == false ? title! : "Saved image" }
    var location: String { boardNames?.map(\.name).joined(separator: ", ") ?? "In your library" }
}

struct MatchResponse: Decodable, Sendable {
    let matches: [SavedMatch]
    let matchState: String
    enum CodingKeys: String, CodingKey { case matches; case matchState = "match_state" }
}

enum LibraryFailure: LocalizedError {
    case invalidURL, authenticationRequired, publicLibrary, invalidResponse, requestFailed(Int), voiceUnavailable
    var errorDescription: String? {
        switch self {
        case .invalidURL: "Enter the HTTPS address of your private Visual Brain library."
        case .authenticationRequired: "Your library is locked. Sign in to continue."
        case .publicLibrary: "Connect a private library. Camera frames cannot be sent to the shared playground."
        case .invalidResponse: "This address did not return a Visual Brain response. Check the library address."
        case .requestFailed(let status): "The library could not finish this request (\(status)). Try again."
        case .voiceUnavailable: "Natural voice is unavailable on this library. Results are shown on your phone."
        }
    }
}

private final class SameOriginRedirects: NSObject, URLSessionTaskDelegate, Sendable {
    let origin: URL
    init(origin: URL) { self.origin = origin }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        guard let target = request.url, LibraryClient.sameOrigin(target, origin) else { completionHandler(nil); return }
        completionHandler(request)
    }
}

final class LibraryClient: Sendable {
    let origin: URL
    private let session: URLSession

    static func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.scheme?.lowercased() == rhs.scheme?.lowercased()
            && lhs.host?.lowercased() == rhs.host?.lowercased()
            && (lhs.port ?? 443) == (rhs.port ?? 443)
    }

    static func validatedOrigin(_ value: String) throws -> URL {
        guard let components = URLComponents(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              components.scheme?.lowercased() == "https", let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil, components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/",
              let origin = components.url else { throw LibraryFailure.invalidURL }
        return origin
    }

    init(address: String) throws {
        origin = try Self.validatedOrigin(address)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = true
        configuration.httpCookieAcceptPolicy = .always
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 45
        session = URLSession(configuration: configuration, delegate: SameOriginRedirects(origin: origin), delegateQueue: nil)
    }

    private func endpoint(_ path: String) -> URL { origin.appendingPathComponent(path) }

    private func request(_ path: String, body: Data? = nil, contentType: String? = nil) -> URLRequest {
        var request = URLRequest(url: endpoint(path))
        if let body {
            request.httpMethod = "POST"
            request.httpBody = body
            request.setValue(origin.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")), forHTTPHeaderField: "Origin")
            request.setValue(contentType ?? "application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw LibraryFailure.invalidResponse }
        if response.statusCode == 401 || response.statusCode == 403 { throw LibraryFailure.authenticationRequired }
        guard (200...299).contains(response.statusCode) else {
            if response.statusCode == 503, request.url?.path == "/api/glasses/voice" { throw LibraryFailure.voiceUnavailable }
            throw LibraryFailure.requestFailed(response.statusCode)
        }
        return (data, response)
    }

    func signIn(passphrase: String) async throws -> LibraryStatus {
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "passphrase", value: passphrase)]
        let body = Data((form.percentEncodedQuery ?? "").replacingOccurrences(of: "+", with: "%2B").utf8)
        _ = try await data(for: request("api/login", body: body, contentType: "application/x-www-form-urlencoded"))
        return try await status()
    }

    func status() async throws -> LibraryStatus {
        let (body, response) = try await data(for: request("api/glasses/status"))
        guard response.value(forHTTPHeaderField: "Content-Type")?.contains("application/json") == true,
              let value = try? JSONDecoder().decode(LibraryStatus.self, from: body) else { throw LibraryFailure.authenticationRequired }
        guard value.privateLibrary else { throw LibraryFailure.publicLibrary }
        return value
    }

    func matches(for jpeg: Data) async throws -> MatchResponse {
        let body = try JSONEncoder().encode(["image_data_url": "data:image/jpeg;base64," + jpeg.base64EncodedString()])
        let (data, response) = try await data(for: request("api/camera-match", body: body))
        guard response.value(forHTTPHeaderField: "Content-Type")?.contains("application/json") == true,
              let matches = try? JSONDecoder().decode(MatchResponse.self, from: data) else { throw LibraryFailure.invalidResponse }
        return matches
    }

    func naturalVoice(text: String) async throws -> Data {
        let body = try JSONEncoder().encode(["text": text])
        let (data, response) = try await data(for: request("api/glasses/voice", body: body))
        guard response.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("audio/") == true, !data.isEmpty else {
            throw LibraryFailure.voiceUnavailable
        }
        return data
    }

    func imageURL(for match: SavedMatch) -> URL { endpoint("api/assets/\(match.id)/thumbnail") }

    func imageData(for match: SavedMatch) async throws -> Data {
        let (body, response) = try await data(for: URLRequest(url: imageURL(for: match)))
        guard response.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("image/") == true else { throw LibraryFailure.invalidResponse }
        return body
    }

    func boardURL(for match: SavedMatch) -> URL? {
        guard let board = match.boards.first, var components = URLComponents(url: endpoint("board"), resolvingAgainstBaseURL: false) else { return nil }
        components.queryItems = [URLQueryItem(name: "board", value: board), URLQueryItem(name: "item", value: match.id)]
        return components.url
    }

    func signOut() {
        session.configuration.httpCookieStorage?.removeCookies(since: .distantPast)
        session.invalidateAndCancel()
    }
}

extension LibraryClient {
    var sessionCookies: [HTTPCookie] { session.configuration.httpCookieStorage?.cookies ?? [] }
}
