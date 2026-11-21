import Foundation
import MWDATCore
import MWDATCamera
import UIKit

@MainActor
final class MetaGlassesSource: FrameSource {
    var frames: AsyncStream<CameraFrame> { output.makeStream() }
    var onStateChange: ((FrameSourceState) -> Void)?
    private var output = FrameChannel()
    private var runID = UUID()
    private var wearables: (any WearablesInterface)?
    private var deviceSession: DeviceSession?
    private var camera: MWDATCamera.Camera?
    private var tokens: [any AnyListenerToken] = []
    private var registrationToken: (any AnyListenerToken)?
    private var requested = false
    private var initialError: String?

    init() {
        do {
            try Wearables.configure()
            wearables = Wearables.shared
            registrationToken = wearables?.addRegistrationStateListener { [weak self] registration in
                Task { @MainActor [weak self] in
                    guard let self, !requested else { return }
                    onStateChange?(registration == .registered ? .ready : .idle)
                }
            }
        } catch { initialError = error.localizedDescription }
    }

    func connect() async throws {
        guard let wearables else { throw CameraFailure.unavailable(initialError ?? "Meta glasses setup is unavailable.") }
        onStateChange?(.preparing("Continue in Meta AI"))
        if wearables.registrationState != .registered { try await wearables.startRegistration() }
        if wearables.registrationState == .registered { onStateChange?(.ready) }
    }

    func handleCallback(_ url: URL) async throws {
        guard url.scheme == "visualbrain", let wearables else { return }
        _ = try await wearables.handleUrl(url)
        if wearables.registrationState == .registered { onStateChange?(.ready) }
    }

    func start() async throws {
        guard let wearables else { throw CameraFailure.unavailable(initialError ?? "Meta glasses setup is unavailable.") }
        guard wearables.registrationState == .registered else {
            throw CameraFailure.unavailable("Connect your glasses through Meta AI first.")
        }
        guard deviceSession == nil else { return }
        requested = true
        runID = UUID()
        let run = runID
        let channel = output
        onStateChange?(.preparing("Connecting to your glasses"))
        let session = try wearables.createSession(deviceSelector: AutoDeviceSelector(wearables: wearables))
        deviceSession = session
        tokens.append(session.statePublisher.listen { [weak self] state in
            Task { @MainActor [weak self] in
                guard let self, requested, runID == run, deviceSession === session else { return }
                switch state {
                case .started:
                    do { try await beginStream(session: session, run: run, output: channel) }
                    catch {
                        guard requested, runID == run, deviceSession === session else { return }
                        onStateChange?(.failed(error.localizedDescription))
                        await stopAfterFailure()
                    }
                case .paused: onStateChange?(.preparing("Glasses paused. Check the fit and connection."))
                case .stopped: onStateChange?(.failed("Glasses disconnected. Tap Start looking to reconnect.")); await stopAfterFailure()
                default: break
                }
            }
        })
        tokens.append(session.errorPublisher.listen { [weak self] error in
            Task { @MainActor [weak self] in
                guard let self, requested, runID == run else { return }
                onStateChange?(.failed(error.localizedDescription))
                await stopAfterFailure()
            }
        })
        do { try session.start() }
        catch { await stopAfterFailure(); throw error }
    }

    private func beginStream(session: DeviceSession, run: UUID, output: FrameChannel) async throws {
        guard camera == nil, let wearables else { return }
        if try await wearables.checkPermissionStatus(.camera) != .granted {
            onStateChange?(.preparing("Allow camera access in Meta AI"))
            guard try await wearables.requestPermission(.camera) == .granted else {
                throw CameraFailure.unavailable("Glasses camera access was denied. Allow it in Meta AI, then retry.")
            }
        }
        guard requested, runID == run, deviceSession === session, camera == nil else { return }
        guard let camera = try session.addCamera(config: StreamConfiguration(videoCodec: .raw, resolution: .medium, frameRate: 7)) else {
            throw CameraFailure.unavailable("The glasses camera is unavailable. Check Meta AI and retry.")
        }
        self.camera = camera
        tokens.append(camera.stream.videoFramePublisher.listen { frame in
            guard let image = frame.makeUIImage(), let jpeg = image.jpegData(compressionQuality: 0.72) else { return }
            output.yield(CameraFrame(jpeg: jpeg, capturedAt: Date()))
        })
        tokens.append(camera.stream.statePublisher.listen { [weak self] state in
            Task { @MainActor [weak self] in
                guard let self, requested, runID == run else { return }
                switch state {
                case .streaming: onStateChange?(.streaming)
                case .waitingForDevice: onStateChange?(.preparing("Put on your glasses and check Meta AI"))
                case .paused: onStateChange?(.preparing("Glasses camera paused"))
                case .stopped: onStateChange?(.failed("Glasses stream stopped. Tap Start looking to retry.")); await stopAfterFailure()
                default: break
                }
            }
        })
        tokens.append(camera.stream.errorPublisher.listen { [weak self] error in
            Task { @MainActor [weak self] in
                guard let self, requested, runID == run else { return }
                onStateChange?(.failed(error.localizedDescription))
                await stopAfterFailure()
            }
        })
        camera.stream.start()
    }

    private func stopAfterFailure() async { await tearDown() }

    private func tearDown() async {
        requested = false
        runID = UUID()
        let oldCamera = camera
        let oldSession = deviceSession
        let oldTokens = tokens
        let oldOutput = output
        camera = nil
        deviceSession = nil
        tokens = []
        output = FrameChannel()
        oldOutput.finish()
        oldCamera?.stream.stop()
        oldSession?.stop()
        for token in oldTokens { await token.cancel() }
    }

    func stop() async {
        onStateChange?(.idle)
        await tearDown()
    }
}
