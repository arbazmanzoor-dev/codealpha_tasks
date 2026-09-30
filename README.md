# 🌐 Language Translation Tool

A Streamlit web app that translates text between 24 languages, including Hindi, Urdu, Kannada, Tamil, French, Spanish, German, Arabic, Japanese and Chinese. It can also read the translation aloud.

## Features

- Source and target language dropdowns, with **Auto-detect** as a source option
- **Translate** button, with the result shown in a styled output box (right-to-left for Arabic, Urdu and Persian)
- **⇄ Swap** button: swaps the two languages and moves the last translation into the input box
- **📋 Copy** button for the translation
- **🔊 Listen**: text-to-speech with gTTS, played in the app
- Live character counter that turns red past the 5,000-character limit
- Clear error messages for empty input, unsupported languages, text that is too long, rate limits and network failures
- A translation backend you can swap with one environment variable. The default needs no API key.

## Screenshots

> _Placeholder: add screenshots to `docs/screenshots/` and link them here._

| Translate | Text-to-speech |
|---|---|
| ![Main screen](docs/screenshots/main.png) | ![Listen](docs/screenshots/listen.png) |

## Project structure

```
app.py            Streamlit UI
translator.py     Translation backends, language list, validation, CLI
tts.py            Text-to-speech (gTTS → MP3 bytes)
requirements.txt
README.md
```

## Setup

Requires Python 3.10 or newer.

```bash
cd language-translator
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then open http://localhost:8501.

### Command-line translation

`translator.py` doubles as a small CLI, which is handy for checking the backend works:

```bash
python translator.py "Good morning" --to hi
python translator.py "Bonjour" --from fr --to en
```

## Using the official Google Cloud Translation API (recommended)

The free endpoint can be rate-limited or blocked. The official API is reliable, and its first 500,000 characters each month are free (see [pricing](https://cloud.google.com/translate/pricing)).

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project, or pick an existing one.
2. Set up billing for the project. Google requires this even for free-tier use.
3. Enable the **Cloud Translation API**: *APIs & Services → Library → Cloud Translation API → Enable*.
4. Create a key: *APIs & Services → Credentials → Create credentials → API key*. Under *API restrictions*, restrict the key to the Cloud Translation API.
5. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and paste in the key:

   ```toml
   GOOGLE_TRANSLATE_API_KEY = "AIza..."
   ```

   Or export it as an environment variable: `export GOOGLE_TRANSLATE_API_KEY=AIza...`
6. Restart the app. The caption under the title should read **Backend: Google Cloud Translation**.

Once the key is set, the app uses it automatically, and the free endpoint and MyMemory fallback are not used. `secrets.toml` is git-ignored, so don't commit the key anywhere else.

## Choosing a translation backend

The app talks to a `BaseTranslator` interface. `get_translator()` picks the implementation from the `TRANSLATOR_BACKEND` environment variable:

| `TRANSLATOR_BACKEND` | Service | Needs |
|---|---|---|
| `google_free` (default) | Google Translate via `deep-translator`, falling back to MyMemory | Nothing |
| `mymemory` | [MyMemory](https://mymemory.translated.net/) free API | Nothing. Optionally set `MYMEMORY_EMAIL` to raise the daily quota from about 5k to 50k words |
| `google_cloud` (default when a key is set) | Google Cloud Translation API (Basic, v2) | `GOOGLE_TRANSLATE_API_KEY`, **or** `GOOGLE_APPLICATION_CREDENTIALS` set to a service-account JSON key plus `pip install google-cloud-translate` |
| `microsoft` | Microsoft Translator (Azure AI Translator) | `MICROSOFT_TRANSLATOR_KEY`, plus `MICROSOFT_TRANSLATOR_REGION` unless your resource is global |

Examples:

```bash
TRANSLATOR_BACKEND=google_cloud GOOGLE_APPLICATION_CREDENTIALS=~/keys/translate.json streamlit run app.py
```

```bash
TRANSLATOR_BACKEND=microsoft MICROSOFT_TRANSLATOR_KEY=... MICROSOFT_TRANSLATOR_REGION=centralindia streamlit run app.py
```

The `google_cloud` API-key path has been tested against Google's live endpoint for request format and error handling, but not yet with a valid key. The service-account path and the `microsoft` backend are **stubs**: they check their configuration, but they haven't been tested against live accounts. To add another provider, subclass `BaseTranslator`, implement `_translate(text, source, target)`, and register the class in `BACKENDS` in `translator.py`. Input validation and network-error handling come from the base class.

## Limits and notes

- **5,000 characters** per request. This is the limit of Google's free endpoint.
- Google's free endpoint is unofficial and rate-limited. If you send too many requests, or Google flags your network, it answers with HTTP 429, and a block can last hours. When that happens, the default backend automatically switches to **MyMemory**. It then skips Google for 5 minutes, and says so in the app under the translation. MyMemory has no auto-detect, so the language is detected offline with `langdetect`. Long text is sent in 500-character chunks. Quality is lower than Google's for some pairs (English→Japanese especially). Set `TRANSLATOR_FALLBACK=none` to turn the fallback off. For steady or heavy use, switch to an official backend.
- Text-to-speech covers most languages in the list. Persian is not supported by gTTS, and the app says so instead of showing the Listen button. Audio is limited to 3,000 characters.
- Both translation and speech need an internet connection.
