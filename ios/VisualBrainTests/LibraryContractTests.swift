import XCTest
@testable import VisualBrain

final class LibraryContractTests: XCTestCase {
    func testLibraryAddressMustBePrivateOriginWithoutEmbeddedSecrets() throws {
        XCTAssertEqual(try LibraryClient.validatedOrigin("https://library.example:9443/").host, "library.example")
        for address in ["http://library.example", "https://owner:secret@library.example", "https://library.example/board", "https://library.example?token=value", "https://library.example/#board"] {
            XCTAssertThrowsError(try LibraryClient.validatedOrigin(address))
        }
    }

    func testOriginBoundaryRejectsRedirectsToAnotherHostOrPort() throws {
        let origin = try LibraryClient.validatedOrigin("https://library.example")
        XCTAssertTrue(LibraryClient.sameOrigin(origin, URL(string: "https://library.example:443/board")!))
        XCTAssertFalse(LibraryClient.sameOrigin(origin, URL(string: "https://other.example/board")!))
        XCTAssertFalse(LibraryClient.sameOrigin(origin, URL(string: "http://library.example/board")!))
        XCTAssertFalse(LibraryClient.sameOrigin(origin, URL(string: "https://library.example:9443/board")!))
    }

    func testFrameChannelCanRestartAfterStop() async {
        let channel = FrameChannel()
        let first = channel.makeStream()
        channel.yield(CameraFrame(jpeg: Data([1]), capturedAt: Date()))
        var firstIterator = first.makeAsyncIterator()
        let firstFrame = await firstIterator.next()
        XCTAssertEqual(firstFrame?.jpeg, Data([1]))
        channel.finish()
        let second = channel.makeStream()
        channel.yield(CameraFrame(jpeg: Data([2]), capturedAt: Date()))
        var secondIterator = second.makeAsyncIterator()
        let secondFrame = await secondIterator.next()
        XCTAssertEqual(secondFrame?.jpeg, Data([2]))
        channel.finish()
    }

    func testResultPreviewUsesBoundedAuthenticatedThumbnail() throws {
        let client = try LibraryClient(address: "https://library.example")
        let match = SavedMatch(id: "saved-1", similarity: 0.4, boards: ["board-1"], title: nil, boardNames: nil, closeMatch: false)
        XCTAssertEqual(client.imageURL(for: match).path, "/api/assets/saved-1/thumbnail")
        client.signOut()
    }

    func testMatchContractKeepsNeighborsSeparateFromCloseMatches() throws {
        let payload = Data(#"{"match_state":"neighbors","matches":[{"id":"image-1","similarity":0.4,"boards":["board-1"],"title":"Saved chair","board_names":[{"id":"board-1","name":"Interior ideas"}],"close_match":false}]}"#.utf8)
        let response = try JSONDecoder().decode(MatchResponse.self, from: payload)
        XCTAssertEqual(response.matchState, "neighbors")
        XCTAssertEqual(response.matches.first?.closeMatch, false)
        XCTAssertEqual(response.matches.first?.location, "Interior ideas")
    }
}
