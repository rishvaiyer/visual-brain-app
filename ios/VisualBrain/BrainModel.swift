import AVFoundation
import Combine
import Foundation
import UIKit

enum CaptureMode: String, CaseIterable, Identifiable {
    case glasses = "Meta glasses"
    case phone = "iPhone"
    var id: String { rawValue }
}

@MainActor
final class BrainModel: ObservableObject {
    @Published private(set) var mode: CaptureMode = .glasses
    @Published private(set) var sourceState: FrameSourceState = .idle
    @Published private(set) var active = false
    @Published private(set) var starting = false
    @Published private(set) var stopping = false
    @Published private(set) var connecting = false
    @Published private(set) var matching = false
    @Published private(set) var preview: UIImage?
    @Published private(set) var matches: [SavedMatch] = []
    @Published private(set) var thumbnails: [String: UIImage] = [:]
    @Published private(set) var headline = "Your library, in view."
    @Published private(set) var detail = "Connect your private library, then choose your glasses or iPhone camera."
    @Published private(set) var errorMessage: String?
    @Published private(set) var client: LibraryClient?
    @Published private(set) var voiceAvailable = false
    @Published var voiceEnabled = false {
        didSet { if !voiceEnabled { voiceTask?.cancel(); audioPlayer?.stop() } }
    }
    @Published var continuous = true
    @Published var libraryAddress = UserDefaults.standard.string(forKey: "libraryAddress") ?? ""
    @Published var passphrase = ""
    @Published var showConnection = false
    @Published private(set) var voiceMessage = "Voice is off."

    private let phone = IPhoneCameraSource()
    private let glasses = MetaGlassesSource()
    private var framesTask: Task<Void, Never>?
    private var matchTask: Task<Void, Never>?
    private var voiceTask: Task<Void, Never>?
    private var thumbnailTask: Task<Void, Never>?
    private var startTask: Task<Void, Never>?
    private var stopTask: Task<Void, Never>?
    private var latestFrame: CameraFrame?
    private var lastMatch = Date.distantPast
    private var lastSpokenID: String?
    private var audioPlayer: AVAudioPlayer?
    private var generation = UUID()

    private var source: any FrameSource { mode == .glasses ? glasses : phone }
    var libraryName: String { client?.origin.host ?? "Connect library" }
    var canCompare: Bool { active && sourceState == .streaming && !matching }

    init() {
        phone.onStateChange = { [weak self] state in self?.receive(state, from: .phone) }
        glasses.onStateChange = { [weak self] state in self?.receive(state, from: .glasses) }
    }

    private func receive(_ state: FrameSourceState, from mode: CaptureMode) {
        guard self.mode == mode else { return }
        sourceState = state
        if case .failed(let message) = state {
            errorMessage = message
            active = false
            framesTask?.cancel()
            matchTask?.cancel()
            voiceTask?.cancel()
            audioPlayer?.stop()
            preview = nil
            latestFrame = nil
            matching = false
        }
    }

    func connectLibrary() async {
        guard !connecting else { return }
        connecting = true
        errorMessage = nil
        defer { connecting = false }
        await stop()
        do {
            let candidate = try LibraryClient(address: libraryAddress)
            let status: LibraryStatus
            if passphrase.isEmpty { status = try await candidate.status() }
            else { status = try await candidate.signIn(passphrase: passphrase) }
            await stop()
            client?.signOut()
            client = candidate
            matches = []
            thumbnails = [:]
            lastSpokenID = nil
            voiceEnabled = false
            passphrase = ""
            libraryAddress = candidate.origin.absoluteString
            UserDefaults.standard.set(libraryAddress, forKey: "libraryAddress")
            voiceAvailable = status.voiceAvailable
            voiceMessage = status.voiceAvailable ? "Voice is off." : "Natural voice is unavailable on this library."
            showConnection = false
            headline = "Find what you saved."
            detail = "Compare a live view with your saved images. Frames are compared by appearance and are not saved."
        } catch { errorMessage = error.localizedDescription }
    }

    func connectGlasses() async {
        errorMessage = nil
        do { try await glasses.connect() }
        catch { errorMessage = error.localizedDescription }
    }

    func handleCallback(_ url: URL) async {
        guard url.scheme?.lowercased() == "visualbrain" else { return }
        if url.host?.lowercased() == "connect" {
            do {
                guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                      components.queryItems?.count == 1,
                      let item = components.queryItems?.first, item.name == "server", let address = item.value else {
                    throw LibraryFailure.invalidURL
                }
                let origin = try LibraryClient.validatedOrigin(address)
                let changedOrigin = client.map { !LibraryClient.sameOrigin($0.origin, origin) } ?? true
                if changedOrigin { await signOut() }
                else { await stop() }
                libraryAddress = origin.absoluteString
                passphrase = ""
                errorMessage = nil
                showConnection = true
            } catch { errorMessage = error.localizedDescription; showConnection = true }
            return
        }
        do { try await glasses.handleCallback(url) }
        catch { errorMessage = error.localizedDescription }
    }

    func selectMode(_ next: CaptureMode) async {
        guard next != mode else { return }
        await stop()
        mode = next
        sourceState = .idle
        errorMessage = nil
    }

    func start() {
        guard !active, !starting, !stopping else { return }
        guard let client else { showConnection = true; return }
        errorMessage = nil
        starting = true
        sourceState = .preparing("Checking private library")
        generation = UUID()
        let run = generation
        startTask = Task {
            defer { if generation == run { starting = false } }
            do {
                let status = try await client.status()
                guard !Task.isCancelled, generation == run else { return }
                voiceAvailable = status.voiceAvailable
                active = true
                lastMatch = .distantPast
                lastSpokenID = nil
                let source = source
                framesTask = Task {
                    for await frame in source.frames {
                        guard !Task.isCancelled, active, generation == run else { break }
                        guard Date().timeIntervalSince(frame.capturedAt) < 2 else { continue }
                        latestFrame = frame
                        preview = UIImage(data: frame.jpeg)
                        if continuous, !matching, Date().timeIntervalSince(lastMatch) >= 4 {
                            compareLatest()
                        }
                    }
                }
                try await source.start()
            } catch {
                guard generation == run, !Task.isCancelled else { return }
                errorMessage = error.localizedDescription
                await stop()
            }
        }
    }

    func compareLatest() {
        guard active, !matching, let frame = latestFrame, let client else { return }
        guard Date().timeIntervalSince(frame.capturedAt) < 2 else {
            errorMessage = "Waiting for a fresh camera frame. Check the camera connection."
            return
        }
        latestFrame = nil
        matching = true
        lastMatch = Date()
        let run = generation
        matchTask = Task {
            do {
                let response = try await client.matches(for: frame.jpeg)
                guard !Task.isCancelled, active, generation == run else { return }
                matches = Array(response.matches.prefix(6))
                errorMessage = nil
                headline = response.matchState == "close" ? "Similar saved images" : response.matches.isEmpty ? "No similar images yet" : "Visual neighbors"
                detail = response.matchState == "close" ? "Check the image and board before reopening." : response.matches.isEmpty ? "Try another angle, or compare something you have saved." : "These share visual features. Review them on your phone."
                loadThumbnails(client: client, matches: matches, run: run)
                if voiceEnabled, let best = matches.first, lastSpokenID != best.id {
                    speak("Similar saved images. \(best.displayTitle). \(best.location).", bestID: best.id, client: client, run: run)
                }
            } catch {
                guard !Task.isCancelled, generation == run else { return }
                errorMessage = error.localizedDescription
                if let failure = error as? LibraryFailure, case .authenticationRequired = failure {
                    await stop()
                    showConnection = true
                }
            }
            if generation == run { matching = false }
        }
    }

    private func loadThumbnails(client: LibraryClient, matches: [SavedMatch], run: UUID) {
        thumbnailTask?.cancel()
        thumbnailTask = Task {
            for match in matches {
                guard !Task.isCancelled, generation == run else { return }
                if thumbnails[match.id] != nil { continue }
                if let data = try? await client.imageData(for: match), let image = UIImage(data: data), generation == run {
                    thumbnails[match.id] = image
                }
            }
            let retained = Set(matches.map(\.id))
            thumbnails = thumbnails.filter { retained.contains($0.key) }
        }
    }

    private func speak(_ text: String, bestID: String, client: LibraryClient, run: UUID) {
        guard voiceEnabled, voiceAvailable else { return }
        voiceTask?.cancel()
        audioPlayer?.stop()
        voiceMessage = "Preparing natural voice…"
        voiceTask = Task {
            do {
                let audio = try await client.naturalVoice(text: String(text.prefix(240)))
                guard !Task.isCancelled, active, voiceEnabled, generation == run else { return }
                try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
                try AVAudioSession.sharedInstance().setActive(true)
                let player = try AVAudioPlayer(data: audio)
                guard player.prepareToPlay(), player.play() else { throw LibraryFailure.voiceUnavailable }
                audioPlayer = player
                lastSpokenID = bestID
                voiceMessage = "Natural voice on."
            } catch {
                guard !Task.isCancelled, generation == run else { return }
                voiceEnabled = false
                voiceMessage = "Natural voice failed. Read results on your phone, or retry voice."
            }
        }
    }

    func stop() async {
        if let stopTask { await stopTask.value; return }
        stopping = true
        generation = UUID()
        active = false
        starting = false
        startTask?.cancel()
        framesTask?.cancel()
        matchTask?.cancel()
        thumbnailTask?.cancel()
        voiceTask?.cancel()
        audioPlayer?.stop()
        audioPlayer = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        latestFrame = nil
        preview = nil
        matching = false
        sourceState = .idle
        let task = Task { @MainActor [phone, glasses] in
            await phone.stop()
            await glasses.stop()
        }
        stopTask = task
        await task.value
        stopTask = nil
        stopping = false
    }

    func signOut() async {
        await stop()
        client?.signOut()
        client = nil
        passphrase = ""
        matches = []
        thumbnails = [:]
        voiceEnabled = false
        voiceAvailable = false
        lastSpokenID = nil
        headline = "Your library, in view."
        detail = "Connect your private library to begin."
    }
}
