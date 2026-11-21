import unittest
from unittest.mock import Mock
import requests

from glasses import GlassesVoice, VoiceError, annotate_matches


class AudioResponse:
    status_code = 200
    headers = {"Content-Type": "audio/mpeg"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, _):
        yield b"unit-test-audio"


class GlassesVoiceTests(unittest.TestCase):
    def service(self, post=None, clock=None):
        return GlassesVoice(environment={"ELEVENLABS_API_KEY": "unit-test-only",
                                        "ELEVENLABS_VOICE_ID": "unit_voice_123"},
                            post=post or Mock(return_value=AudioResponse()), clock=clock)

    def test_public_library_and_missing_configuration_never_contact_provider(self):
        post = Mock()
        with self.assertRaises(VoiceError):
            self.service(post).speak("Saved reference", private_library=False)
        with self.assertRaises(VoiceError):
            GlassesVoice(environment={}, post=post).speak("Saved reference", private_library=True)
        post.assert_not_called()

    def test_natural_audio_is_cached_without_repeating_billable_requests(self):
        post = Mock(return_value=AudioResponse())
        service = self.service(post)
        self.assertEqual(service.speak("Saved reference", private_library=True), b"unit-test-audio")
        self.assertEqual(service.speak(" Saved   reference ", private_library=True), b"unit-test-audio")
        self.assertEqual(post.call_count, 1)
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["json"]["text"], "Saved reference")

    def test_voice_failure_never_returns_robotic_fallback_or_provider_error(self):
        post = Mock(side_effect=requests.ConnectionError("provider-private-detail"))
        with self.assertRaises(VoiceError) as error:
            self.service(post).speak("Saved reference", private_library=True)
        self.assertNotIn("provider-private-detail", str(error.exception))
        self.assertIn("Visual results", str(error.exception))

    def test_attempt_budget_is_shared_and_recovers_on_later_requests(self):
        clock = [0]
        post = Mock(return_value=AudioResponse())
        service = self.service(post, lambda: clock[0])
        for i in range(6):
            service.speak(str(i), private_library=True)
        with self.assertRaises(VoiceError) as error:
            service.speak("seventh", private_library=True)
        self.assertEqual(error.exception.status, 429)
        self.assertEqual(post.call_count, 6)
        clock[0] = 61
        service.speak("later request", private_library=True)
        self.assertEqual(post.call_count, 7)

    def test_invalid_text_and_provider_payloads_are_refused(self):
        post = Mock()
        service = self.service(post)
        for text in [None, "", " " * 3, "x" * 241]:
            with self.assertRaises(VoiceError) as error:
                service.speak(text, private_library=True)
            self.assertEqual(error.exception.status, 400)
        post.assert_not_called()
        response = AudioResponse()
        response.headers = {"Content-Type": "application/json"}
        with self.assertRaises(VoiceError):
            self.service(Mock(return_value=response)).speak("Hello", private_library=True)

    def test_appearance_neighbors_are_not_labeled_identified_objects(self):
        self.assertEqual(annotate_matches([]), "empty")
        matches = [{"id": "one", "similarity": .6}]
        self.assertEqual(annotate_matches(matches), "neighbors")
        self.assertFalse(matches[0]["close_match"])
        self.assertEqual(annotate_matches([{"id": "same", "similarity": 1}]), "close")


if __name__ == "__main__":
    unittest.main()
