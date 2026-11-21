import AVFoundation
import CoreImage
import UIKit

private final class PhoneCaptureDriver: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    let session = AVCaptureSession()
    let queue = DispatchQueue(label: "com.visualbrain.phone-camera", qos: .userInitiated)
    let output: FrameChannel
    private let imageContext = CIContext(options: [.cacheIntermediates: false])
    private var configured = false
    private var lastFrame = Date.distantPast
    private var observers: [NSObjectProtocol] = []
    var onFailure: (@Sendable (String) -> Void)?

    init(output: FrameChannel) { self.output = output }

    func start() async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async { [self] in
                do {
                    if !configured {
                        session.beginConfiguration()
                        defer { session.commitConfiguration() }
                        session.sessionPreset = .medium
                        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
                            throw CameraFailure.unavailable("This device has no available back camera.")
                        }
                        let input = try AVCaptureDeviceInput(device: camera)
                        guard session.canAddInput(input) else { throw CameraFailure.unavailable("The camera is in use.") }
                        session.addInput(input)
                        let video = AVCaptureVideoDataOutput()
                        video.alwaysDiscardsLateVideoFrames = true
                        video.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
                        video.setSampleBufferDelegate(self, queue: queue)
                        guard session.canAddOutput(video) else { throw CameraFailure.unavailable("Camera frames are unavailable.") }
                        session.addOutput(video)
                        if let connection = video.connection(with: .video), connection.isVideoRotationAngleSupported(90) {
                            connection.videoRotationAngle = 90
                        }
                        observers.append(NotificationCenter.default.addObserver(forName: .AVCaptureSessionRuntimeError, object: session, queue: nil) { [weak self] _ in
                            self?.onFailure?("The iPhone camera stopped. Close other camera apps, then retry.")
                        })
                        observers.append(NotificationCenter.default.addObserver(forName: .AVCaptureSessionWasInterrupted, object: session, queue: nil) { [weak self] _ in
                            self?.onFailure?("The iPhone camera was interrupted. Tap Start looking when it is available.")
                        })
                        configured = true
                    }
                    if !session.isRunning { session.startRunning() }
                    guard session.isRunning else { throw CameraFailure.unavailable("The iPhone camera could not start. Try again.") }
                    continuation.resume()
                } catch { continuation.resume(throwing: error) }
            }
        }
    }

    deinit {
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
    }

    func stop() async {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                if session.isRunning { session.stopRunning() }
                continuation.resume()
            }
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = Date()
        guard now.timeIntervalSince(lastFrame) >= 0.2,
              let pixel = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        lastFrame = now
        let image = CIImage(cvPixelBuffer: pixel)
        guard let cgImage = imageContext.createCGImage(image, from: image.extent),
              let jpeg = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.72) else { return }
        self.output.yield(CameraFrame(jpeg: jpeg, capturedAt: now))
    }
}

@MainActor
final class IPhoneCameraSource: FrameSource {
    var frames: AsyncStream<CameraFrame> { channel.makeStream() }
    private var channel = FrameChannel()
    var onStateChange: ((FrameSourceState) -> Void)?
    private var driver: PhoneCaptureDriver?
    private var requested = false
    private var runID = UUID()

    func start() async throws {
        guard !requested else { return }
        requested = true
        runID = UUID()
        let run = runID
        onStateChange?(.preparing("Requesting camera access"))
        let allowed = await AVCaptureDevice.requestAccess(for: .video)
        guard requested, runID == run else { return }
        guard allowed else { throw CameraFailure.unavailable("Camera access is off. Allow Visual Brain in iPhone Settings, then try again.") }
        let driver = PhoneCaptureDriver(output: channel)
        self.driver = driver
        driver.onFailure = { [weak self] message in
            Task { @MainActor [weak self] in
                guard let self, requested, runID == run else { return }
                onStateChange?(.failed(message))
                await stopCamera()
            }
        }
        try await driver.start()
        guard requested, runID == run else { await driver.stop(); return }
        onStateChange?(.streaming)
    }

    private func stopCamera() async {
        requested = false
        runID = UUID()
        let oldDriver = driver
        let oldChannel = channel
        driver = nil
        channel = FrameChannel()
        oldChannel.finish()
        await oldDriver?.stop()
    }

    func stop() async {
        onStateChange?(.idle)
        await stopCamera()
    }
}
