import Foundation
import Vision

guard CommandLine.arguments.count == 2 else { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let classify = VNClassifyImageRequest()
let text = VNRecognizeTextRequest()
text.recognitionLevel = .accurate
do {
    try VNImageRequestHandler(url: url).perform([classify, text])
    let labels = (classify.results ?? [])
        .filter { $0.confidence >= 0.08 }
        .prefix(12)
        .map { $0.identifier.replacingOccurrences(of: "_", with: " ") }
    let words = (text.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    let data = try JSONSerialization.data(withJSONObject: ["labels": labels, "text": words.joined(separator: " ")])
    print(String(decoding: data, as: UTF8.self))
} catch {
    fputs("Vision analysis failed: \(error)\n", stderr)
    exit(1)
}
