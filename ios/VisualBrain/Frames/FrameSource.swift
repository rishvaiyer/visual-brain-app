import Foundation

struct CameraFrame: Sendable {
    let jpeg: Data
    let capturedAt: Date
}

enum FrameSourceState: Equatable, Sendable {
    case idle
    case preparing(String)
    case ready
    case streaming
    case failed(String)

    var label: String {
        switch self {
        case .idle: "Camera off"
        case .preparing(let message): message
        case .ready: "Connected"
        case .streaming: "Looking"
        case .failed(let message): message
        }
    }
}

@MainActor
protocol FrameSource: AnyObject {
    var frames: AsyncStream<CameraFrame> { get }
    var onStateChange: ((FrameSourceState) -> Void)? { get set }
    func start() async throws
    func stop() async
}

enum CameraFailure: LocalizedError {
    case unavailable(String)
    var errorDescription: String? {
        switch self { case .unavailable(let message): message }
    }
}


final class FrameChannel: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: AsyncStream<CameraFrame>.Continuation?

    func makeStream() -> AsyncStream<CameraFrame> {
        let pair = AsyncStream<CameraFrame>.makeStream(bufferingPolicy: .bufferingNewest(1))
        lock.lock()
        let previous = continuation
        continuation = pair.continuation
        lock.unlock()
        previous?.finish()
        return pair.stream
    }

    func yield(_ frame: CameraFrame) {
        lock.lock()
        let current = continuation
        lock.unlock()
        current?.yield(frame)
    }

    func finish() {
        lock.lock()
        let current = continuation
        continuation = nil
        lock.unlock()
        current?.finish()
    }
}
