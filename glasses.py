"""Private companion capabilities and optional natural-voice responses."""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict

import requests


def annotate_matches(matches):
    # A presentation heuristic for visual neighbors, never object identification.
    for match in matches:
        match["close_match"] = match["similarity"] >= .86
    return "empty" if not matches else "close" if matches[0]["close_match"] else "neighbors"


class VoiceError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class GlassesVoice:
    def __init__(self, *, environment=None, post=None, clock=None):
        env = os.environ if environment is None else environment
        self.key = env.get("ELEVENLABS_API_KEY", "")
        self.voice = env.get("ELEVENLABS_VOICE_ID", "")
        self.post = post or requests.post
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(2)
        self.attempts = []
        self.cache = OrderedDict()

    @property
    def configured(self):
        return bool(self.key and re.fullmatch(r"[A-Za-z0-9_-]{8,100}", self.voice))

    def speak(self, text, *, private_library):
        if not private_library or not self.configured:
            raise VoiceError("Natural voice is unavailable. Configure it on your private library; visual results still work.")
        if not isinstance(text, str):
            raise VoiceError("Choose a short spoken result.", 400)
        text = " ".join(text.split())
        if not text or len(text) > 240:
            raise VoiceError("Spoken results must contain 1–240 characters.", 400)
        now = self.clock()
        with self.lock:
            cached = self.cache.get(text)
            if cached and now - cached[0] < 300:
                self.cache.move_to_end(text)
                return cached[1]
            self.attempts = [stamp for stamp in self.attempts if now - stamp < 60]
            if len(self.attempts) >= 6:
                raise VoiceError("Voice is busy. Continue with visual results and try again later.", 429)
            if not self.slots.acquire(blocking=False):
                raise VoiceError("Voice is busy. Visual results are still available.", 429)
            self.attempts.append(now)
        try:
            with self.post(
                "https://api.elevenlabs.io/v1/text-to-speech/" + self.voice,
                params={"output_format": "mp3_22050_32"},
                headers={"xi-api-key": self.key, "Accept": "audio/mpeg"},
                json={"text": text, "model_id": "eleven_flash_v2_5"},
                timeout=(4, 12), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("audio/"):
                    raise VoiceError("Natural voice could not respond. Visual results are still available.")
                chunks = []
                size = 0
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 262144:
                        raise VoiceError("Natural voice returned an invalid response.")
                    chunks.append(chunk)
                audio = b"".join(chunks)
                if not audio:
                    raise VoiceError("Natural voice returned no audio.")
            with self.lock:
                self.cache[text] = (self.clock(), audio)
                self.cache.move_to_end(text)
                while len(self.cache) > 8:
                    self.cache.popitem(last=False)
            return audio
        except requests.RequestException:
            raise VoiceError("Natural voice could not connect. Visual results are still available.") from None
        finally:
            self.slots.release()
