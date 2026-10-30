"""Check the local decision boundary without downloading model weights."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import enrichment


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {"answers": {
            "tag_0": {"type": "noul", "noul": 0.94},
            "tag_1": {"type": "noul", "noul": 0.22},
            "board": {"type": "choice", "choice": "board_a", "confidence": 0.82},
            "collection": {"type": "choice", "choice": "Interiors", "confidence": 0.79},
        }}


asset = {"alt": "A reading room", "title": "Study", "source_url": "https://example.com/page"}
candidates = [{"tag": "interior", "score": 0.7, "source": "image"},
              {"tag": "garden", "score": 0.3, "source": "page text"}]
boards = [{"id": "main", "name": "Everything"}, {"id": "board_a", "name": "Interiors"}]

with patch.object(enrichment, "PROVIDER_URL", "http://127.0.0.1:8009"):
    with patch.object(enrichment.requests, "post", return_value=Response()) as send:
        tags, board, collection, provider = enrichment._decide(asset, candidates, boards, ["Interiors"])
        assert [tag["tag"] for tag in tags] == ["interior"]
        assert board == "board_a" and collection == "Interiors" and provider == "local-decision"
        sent = send.call_args.kwargs["json"]
        assert sent["state"]["alt"] == "A reading room"
        assert "image_data_url" not in str(sent)
        assert len(sent["questions"]) == 4

with patch.object(enrichment, "PROVIDER_URL", "https://example.com"):
    try:
        enrichment._decide(asset, candidates, boards, [])
    except ValueError:
        pass
    else:
        raise AssertionError("Remote decision URL was accepted")

print("PASS optional local decision service contract and remote URL rejection")
