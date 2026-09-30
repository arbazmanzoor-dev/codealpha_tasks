"""Text-to-speech for translated text, using gTTS."""

from __future__ import annotations

from io import BytesIO

import requests


class TTSError(Exception):
    """Raised with a message that can be shown to the user."""


# gTTS text limit is generous, but long audio is slow to generate and play.
MAX_TTS_CHARS = 3000


def _supported_languages() -> dict[str, str]:
    from gtts.lang import tts_langs

    return tts_langs()


def is_supported(lang_code: str) -> bool:
    try:
        return lang_code in _supported_languages()
    except Exception:
        return False


def synthesize(text: str, lang_code: str) -> bytes:
    """Return MP3 bytes of ``text`` spoken in ``lang_code``."""
    from gtts import gTTS
    from gtts.tts import gTTSError

    if not text.strip():
        raise TTSError("There is no text to speak.")
    if len(text) > MAX_TTS_CHARS:
        raise TTSError(f"Text-to-speech is limited to {MAX_TTS_CHARS:,} characters.")
    if not is_supported(lang_code):
        raise TTSError(f"Text-to-speech isn't available for language code '{lang_code}'.")

    buffer = BytesIO()
    try:
        gTTS(text=text, lang=lang_code).write_to_fp(buffer)
    except (gTTSError, requests.ConnectionError, requests.Timeout) as exc:
        raise TTSError(
            "Could not generate audio. Check your internet connection and try again."
        ) from exc
    return buffer.getvalue()
