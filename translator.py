"""Translation logic, kept separate from the UI.

The app talks to a ``BaseTranslator``. Which concrete backend it gets is decided
by the ``TRANSLATOR_BACKEND`` environment variable:

    google_free   (default) Google Translate via deep-translator, no API key
    mymemory      MyMemory (translated.net) free API, no API key
    google_cloud  Official Google Cloud Translation API (v2), needs credentials
    microsoft     Microsoft Translator (Azure AI Translator), needs a key

With ``google_free``, requests fall back to MyMemory when Google refuses them
(rate limiting, or Google flagging the network). ``TRANSLATOR_FALLBACK=none``
turns that off.

Run this file directly for a quick command-line translation:

    python translator.py "Good morning" --to hi
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests

# Google's free web endpoint rejects anything longer than this.
MAX_CHARS = 5000

AUTO_DETECT = "Auto-detect"

# Display name -> language code. Codes follow Google's conventions, which
# deep-translator, gTTS and the Cloud API all accept.
LANGUAGES: dict[str, str] = {
    "English": "en",
    "Hindi": "hi",
    "Urdu": "ur",
    "Kannada": "kn",
    "Tamil": "ta",
    "Telugu": "te",
    "Bengali": "bn",
    "Marathi": "mr",
    "Malayalam": "ml",
    "Gujarati": "gu",
    "Punjabi": "pa",
    "French": "fr",
    "Spanish": "es",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Russian": "ru",
    "Arabic": "ar",
    "Persian": "fa",
    "Turkish": "tr",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese (Simplified)": "zh-CN",
    "Chinese (Traditional)": "zh-TW",
}

# Scripts written right to left, so the UI can set the text direction.
RTL_CODES = {"ar", "ur", "fa", "he", "iw"}


class TranslationError(Exception):
    """Base class for errors the UI should show to the user as-is."""


class EmptyInputError(TranslationError):
    pass


class TextTooLongError(TranslationError):
    pass


class UnsupportedLanguageError(TranslationError):
    pass


class NetworkError(TranslationError):
    pass


class RateLimitError(NetworkError):
    pass


class BackendConfigError(TranslationError):
    pass


@dataclass
class TranslationResult:
    text: str
    backend: str
    detected_source: str | None = None  # language code, when the backend guessed it
    note: str | None = None  # anything the user should know about how it was made


CODE_TO_NAME = {code: name for name, code in LANGUAGES.items()}

_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")


def detect_language(text: str) -> str:
    """Guess the language code of ``text`` offline. Falls back to English."""
    # langdetect is unreliable on short CJK text, and the script settles it.
    if _HANGUL.search(text):
        return "ko"
    if _KANA.search(text):
        return "ja"
    if _HAN.search(text):
        return "zh-CN"
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0  # deterministic results
        code = detect(text)
    except Exception:
        return "en"
    code = {"zh-cn": "zh-CN", "zh-tw": "zh-TW"}.get(code, code)
    return code if code in CODE_TO_NAME else "en"


def language_code(name: str) -> str:
    """Map a display name (or ``Auto-detect``) to a backend language code."""
    if name == AUTO_DETECT:
        return "auto"
    try:
        return LANGUAGES[name]
    except KeyError:
        raise UnsupportedLanguageError(f"'{name}' is not a supported language.") from None


def validate(text: str, source: str, target: str) -> str:
    """Check the request before it goes over the network. Returns stripped text."""
    stripped = text.strip()
    if not stripped:
        raise EmptyInputError("Please enter some text to translate.")
    if len(text) > MAX_CHARS:
        raise TextTooLongError(
            f"Text is {len(text):,} characters; the limit is {MAX_CHARS:,}. "
            "Shorten it or split it into parts."
        )
    if target == "auto":
        raise UnsupportedLanguageError("Auto-detect can only be used as the source language.")
    if source != "auto" and source == target:
        raise UnsupportedLanguageError("Source and target languages are the same.")
    return stripped


class BaseTranslator(ABC):
    """Interface every backend implements."""

    name: str = "base"

    def translate(self, text: str, source: str, target: str) -> TranslationResult:
        """Translate ``text`` from ``source`` to ``target`` (codes; source may be 'auto')."""
        cleaned = validate(text, source, target)
        try:
            result = self._translate(cleaned, source, target)
            if isinstance(result, str):
                result = TranslationResult(result, self.name)
            return result
        except TranslationError:
            raise
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise NetworkError(
                "Could not reach the translation service. Check your internet connection and try again."
            ) from exc

    @abstractmethod
    def _translate(self, text: str, source: str, target: str) -> str | TranslationResult: ...


class GoogleFreeTranslator(BaseTranslator):
    """Google Translate's public endpoint through deep-translator. No key needed."""

    name = "Google Translate (deep-translator)"

    def _translate(self, text: str, source: str, target: str) -> str:
        from deep_translator import GoogleTranslator
        from deep_translator.exceptions import (
            LanguageNotSupportedException,
            NotValidLength,
            NotValidPayload,
            RequestError,
            TooManyRequests,
            TranslationNotFound,
        )

        try:
            result = GoogleTranslator(source=source, target=target).translate(text)
        except LanguageNotSupportedException as exc:
            bad = target if source == "auto" else f"{source}' or '{target}"
            raise UnsupportedLanguageError(
                f"Language '{bad}' is not supported by {self.name}."
            ) from exc
        except NotValidLength as exc:
            raise TextTooLongError(f"Text must be 1 to {MAX_CHARS:,} characters.") from exc
        except NotValidPayload as exc:
            raise EmptyInputError("That input can't be translated. Try entering some words.") from exc
        except TooManyRequests as exc:
            raise RateLimitError(
                "Google Translate is refusing requests from this network (HTTP 429). "
                "This can last from minutes to hours."
            ) from exc
        except (RequestError, TranslationNotFound) as exc:
            raise NetworkError(f"The translation service returned an error: {exc}") from exc

        if not result:
            raise NetworkError("The translation service returned an empty result. Try again.")
        return result


class GoogleCloudTranslator(BaseTranslator):
    """Official Google Cloud Translation API (Basic / v2).

    Authenticates with either of:
    - ``GOOGLE_TRANSLATE_API_KEY``: an API key, sent over REST (no extra packages), or
    - ``GOOGLE_APPLICATION_CREDENTIALS``: a service-account JSON key, used through
      the ``google-cloud-translate`` client library.
    """

    name = "Google Cloud Translation"
    URL = "https://translation.googleapis.com/language/translate/v2"

    def __init__(self) -> None:
        self._key = os.getenv("GOOGLE_TRANSLATE_API_KEY", "").strip()
        self._client = None
        if self._key:
            return
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            raise BackendConfigError(
                "Google Cloud Translation needs GOOGLE_TRANSLATE_API_KEY (an API key) or "
                "GOOGLE_APPLICATION_CREDENTIALS (a service-account JSON key). See the README."
            )
        try:
            from google.cloud import translate_v2
        except ImportError as exc:
            raise BackendConfigError(
                "Service-account auth needs: pip install google-cloud-translate"
            ) from exc
        self._client = translate_v2.Client()

    def _translate(self, text: str, source: str, target: str) -> TranslationResult:
        if self._client is not None:
            data = self._client.translate(
                text,
                target_language=target,
                source_language=None if source == "auto" else source,
                format_="text",
            )
        else:
            data = self._request(text, source, target)
        detected = data.get("detectedSourceLanguage") if source == "auto" else None
        return TranslationResult(data["translatedText"], self.name, detected)

    def _request(self, text: str, source: str, target: str) -> dict:
        payload = {"q": text, "target": target, "format": "text"}
        if source != "auto":
            payload["source"] = source
        # The key goes in a header rather than the URL so it stays out of logs.
        response = requests.post(
            self.URL, data=payload, headers={"X-goog-api-key": self._key}, timeout=15
        )
        if response.ok:
            return response.json()["data"]["translations"][0]

        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = response.text[:200]
        status = response.status_code
        if status == 400 and "api key" in message.lower():
            raise BackendConfigError(f"Google rejected the API key: {message}")
        if status == 400:
            raise UnsupportedLanguageError(f"Google Cloud Translation refused the request: {message}")
        if status in (401, 403):
            raise BackendConfigError(
                f"Google Cloud Translation denied access: {message} "
                "Check that the Cloud Translation API is enabled, billing is set up, "
                "and the key's restrictions allow this API."
            )
        if status == 429:
            raise RateLimitError(f"Google Cloud Translation quota exceeded: {message}")
        raise NetworkError(f"Google Cloud Translation error (HTTP {status}): {message}")


class MicrosoftTranslatorBackend(BaseTranslator):
    """Stub for Microsoft Translator (Azure AI Translator), via deep-translator.

    Setup: create a Translator resource in Azure and set ``MICROSOFT_TRANSLATOR_KEY``
    (and ``MICROSOFT_TRANSLATOR_REGION`` unless the resource is global).
    """

    name = "Microsoft Translator"

    # Microsoft uses different codes for a few languages.
    CODE_MAP = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}

    def __init__(self) -> None:
        self._key = os.getenv("MICROSOFT_TRANSLATOR_KEY")
        self._region = os.getenv("MICROSOFT_TRANSLATOR_REGION")
        if not self._key:
            raise BackendConfigError(
                "TRANSLATOR_BACKEND=microsoft needs MICROSOFT_TRANSLATOR_KEY."
            )

    def _translate(self, text: str, source: str, target: str) -> str:
        from deep_translator import MicrosoftTranslator
        from deep_translator.exceptions import MicrosoftAPIerror

        kwargs = {"api_key": self._key, "target": self.CODE_MAP.get(target, target)}
        if source != "auto":  # omitting source lets Microsoft detect it
            kwargs["source"] = self.CODE_MAP.get(source, source)
        if self._region:
            kwargs["region"] = self._region
        try:
            return MicrosoftTranslator(**kwargs).translate(text)
        except MicrosoftAPIerror as exc:
            raise NetworkError(f"Microsoft Translator returned an error: {exc}") from exc


class MyMemoryBackend(BaseTranslator):
    """MyMemory (api.mymemory.translated.net). Free, no key.

    It has no auto-detect and takes at most 500 characters per request, so this
    class detects the language locally and sends long text in chunks. The free
    quota is about 5,000 words a day; setting ``MYMEMORY_EMAIL`` raises it to
    about 50,000.
    """

    name = "MyMemory"
    URL = "https://api.mymemory.translated.net/get"
    CHUNK_CHARS = 500

    def __init__(self) -> None:
        self._email = os.getenv("MYMEMORY_EMAIL")

    def _translate(self, text: str, source: str, target: str) -> TranslationResult:
        detected = None
        if source == "auto":
            source = detected = detect_language(text)
        if source == target:
            return TranslationResult(text, self.name, detected, note="The text already appears to be in the target language.")
        parts = [self._request(chunk, source, target) for chunk in _chunks(text, self.CHUNK_CHARS)]
        return TranslationResult(" ".join(parts), self.name, detected)

    def _request(self, text: str, source: str, target: str) -> str:
        params = {"q": text, "langpair": f"{source}|{target}"}
        if self._email:
            params["de"] = self._email
        response = requests.get(self.URL, params=params, timeout=15)
        if response.status_code == 429:
            raise RateLimitError("MyMemory's free daily quota is used up. Try again tomorrow.")
        try:
            data = response.json()
        except ValueError:
            raise NetworkError(f"MyMemory returned an unexpected response (HTTP {response.status_code}).") from None

        # Errors come back inside a 200 response, with the status as a string or int.
        status = str(data.get("responseStatus"))
        details = data.get("responseDetails") or ""
        if data.get("quotaFinished") or status == "429":
            raise RateLimitError("MyMemory's free daily quota is used up. Try again tomorrow.")
        if status != "200":
            if "INVALID" in details.upper():
                raise UnsupportedLanguageError(f"MyMemory doesn't support the pair {source} → {target}.")
            raise NetworkError(f"MyMemory returned an error: {details or status}")
        return html.unescape(data["responseData"]["translatedText"])


def _chunks(text: str, size: int) -> list[str]:
    """Split text into pieces of at most ``size`` characters, preferring sentence ends."""
    sentences = re.split(r"(?<=[.!?।。！？\n])\s*", text)
    chunks, current = [], ""
    for sentence in filter(None, sentences):
        while len(sentence) > size:  # a single very long sentence: cut at a space
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            chunks.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > size:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        chunks.append(current)
    return chunks


class FallbackTranslator(BaseTranslator):
    """Try ``primary``; if it fails with a network error, use ``fallback``.

    After a failure the primary is skipped for ``cooldown`` seconds, so every
    request doesn't wait on a service that is refusing us.
    """

    def __init__(self, primary: BaseTranslator, fallback: BaseTranslator, cooldown: float = 300) -> None:
        self.primary, self.fallback, self.cooldown = primary, fallback, cooldown
        self.name = f"{primary.name}, falling back to {fallback.name}"
        self._primary_down_until = 0.0

    def translate(self, text: str, source: str, target: str) -> TranslationResult:
        validate(text, source, target)
        if time.monotonic() >= self._primary_down_until:
            try:
                return self.primary.translate(text, source, target)
            except NetworkError as exc:
                self._primary_down_until = time.monotonic() + self.cooldown
                reason = str(exc)
        else:
            reason = f"{self.primary.name} failed recently and is paused for a few minutes."

        result = self.fallback.translate(text, source, target)
        fallback_note = f"{reason} Translated with {self.fallback.name} instead; quality may be lower."
        result.note = f"{fallback_note} {result.note}" if result.note else fallback_note
        return result

    def _translate(self, text: str, source: str, target: str) -> TranslationResult:
        raise NotImplementedError  # translate() is overridden


BACKENDS: dict[str, type[BaseTranslator]] = {
    "google_free": GoogleFreeTranslator,
    "mymemory": MyMemoryBackend,
    "google_cloud": GoogleCloudTranslator,
    "microsoft": MicrosoftTranslatorBackend,
}


def get_translator(backend: str | None = None) -> BaseTranslator:
    """Build the backend named by ``backend`` or the TRANSLATOR_BACKEND env var.

    With neither set, the official Google Cloud API is used when credentials for it
    are present, and the free Google endpoint otherwise.
    """
    has_cloud_creds = bool(
        os.getenv("GOOGLE_TRANSLATE_API_KEY", "").strip() or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )
    default = "google_cloud" if has_cloud_creds else "google_free"
    key = (backend or os.getenv("TRANSLATOR_BACKEND") or default).strip().lower()
    try:
        cls = BACKENDS[key]
    except KeyError:
        raise BackendConfigError(
            f"Unknown TRANSLATOR_BACKEND '{key}'. Choose one of: {', '.join(BACKENDS)}."
        ) from None
    translator = cls()

    fallback = (os.getenv("TRANSLATOR_FALLBACK") or "mymemory").strip().lower()
    if key == "google_free" and fallback != "none":
        if fallback not in BACKENDS or fallback == key:
            raise BackendConfigError(f"Unknown TRANSLATOR_FALLBACK '{fallback}'. Use 'none' or another backend.")
        translator = FallbackTranslator(translator, BACKENDS[fallback]())
    return translator


def _main() -> int:
    parser = argparse.ArgumentParser(description="Translate text from the command line.")
    parser.add_argument("text")
    parser.add_argument("--from", dest="source", default="auto", help="source code, default auto")
    parser.add_argument("--to", dest="target", default="en", help="target code, default en")
    parser.add_argument("--backend", default=None, help=f"one of {', '.join(BACKENDS)}")
    args = parser.parse_args()

    try:
        translator = get_translator(args.backend)
        result = translator.translate(args.text, args.source, args.target)
        print(result.text)
        detected = f", detected {CODE_TO_NAME.get(result.detected_source)}" if result.detected_source else ""
        print(f"[{result.backend}{detected}]", file=sys.stderr)
        if result.note:
            print(f"Note: {result.note}", file=sys.stderr)
    except TranslationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
