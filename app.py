"""Streamlit UI for the Language Translation Tool.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import html
import json
import os

import streamlit as st

import tts
from translator import (
    AUTO_DETECT,
    CODE_TO_NAME,
    LANGUAGES,
    GoogleCloudTranslator,
    MAX_CHARS,
    RTL_CODES,
    TranslationError,
    get_translator,
    language_code,
)

st.set_page_config(page_title="Language Translator", page_icon="🌐", layout="centered")

st.markdown(
    """
    <style>
    .output-box {
        border: 1px solid rgba(49, 130, 206, 0.45);
        border-left: 5px solid #3182ce;
        background: rgba(49, 130, 206, 0.08);
        border-radius: 10px;
        padding: 1rem 1.2rem;
        font-size: 1.15rem;
        line-height: 1.6;
        white-space: pre-wrap;
        word-wrap: break-word;
    }
    .output-label { font-size: 0.85rem; opacity: 0.7; margin-bottom: 0.35rem; }
    .char-count { font-size: 0.85rem; text-align: right; opacity: 0.75; margin-top: -0.6rem; }
    .char-count.over { color: #e53e3e; opacity: 1; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

SOURCE_OPTIONS = [AUTO_DETECT, *LANGUAGES]
TARGET_OPTIONS = list(LANGUAGES)

ss = st.session_state
ss.setdefault("source", AUTO_DETECT)
ss.setdefault("target", "Hindi")
ss.setdefault("text", "")
ss.setdefault("result", None)  # {"text", "target", "audio"}


CONFIG_NAMES = (
    "TRANSLATOR_BACKEND",
    "TRANSLATOR_FALLBACK",
    "GOOGLE_TRANSLATE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "MICROSOFT_TRANSLATOR_KEY",
    "MICROSOFT_TRANSLATOR_REGION",
    "MYMEMORY_EMAIL",
)


def load_secrets_into_env() -> None:
    """Let settings live in .streamlit/secrets.toml. Real env vars take precedence."""
    for name in CONFIG_NAMES:
        if os.getenv(name):
            continue
        try:
            value = st.secrets.get(name)
        except Exception:  # no secrets file, or it can't be parsed
            return
        if value:
            os.environ[name] = str(value)


@st.cache_resource
def load_translator():
    load_secrets_into_env()
    return get_translator()


@st.cache_data(show_spinner=False, max_entries=64)
def speak(text: str, lang_code: str) -> bytes:
    return tts.synthesize(text, lang_code)


def swap_languages() -> None:
    """Swap source/target, and move the last translation into the input box."""
    old_source, old_target = ss.source, ss.target
    ss.source = old_target
    if old_source == AUTO_DETECT:
        # The detected language isn't known, so pick a sensible new target.
        ss.target = "English" if old_target != "English" else "Hindi"
    else:
        ss.target = old_source
    if ss.result:
        ss.text = ss.result["text"]
        ss.result = None


def copy_button(text: str) -> None:
    """A small button that copies ``text`` to the clipboard (runs in an iframe)."""
    payload = json.dumps(text).replace("</", "<\\/")
    st.iframe(
        f"""
        <button id="copy" style="
            font: 500 14px system-ui, sans-serif; padding: 6px 14px; cursor: pointer;
            border-radius: 8px; border: 1px solid #9aa5b1; background: transparent; color: #3182ce;">
            📋 Copy translation
        </button>
        <script>
        const text = {payload};
        const btn = document.getElementById("copy");
        function fallback() {{
            const ta = document.createElement("textarea");
            ta.value = text; document.body.appendChild(ta); ta.select();
            const ok = document.execCommand("copy"); ta.remove(); return ok;
        }}
        btn.addEventListener("click", async () => {{
            let ok = false;
            try {{ await navigator.clipboard.writeText(text); ok = true; }}
            catch (e) {{ ok = fallback(); }}
            btn.textContent = ok ? "✅ Copied!" : "⚠️ Copy failed";
            setTimeout(() => (btn.textContent = "📋 Copy translation"), 1800);
        }});
        </script>
        """,
        height=44,
    )


st.title("🌐 Language Translator")

try:
    translator = load_translator()
except TranslationError as exc:
    st.error(f"Translator backend isn't configured: {exc}")
    st.stop()

st.caption(f"Backend: {translator.name}")
if not isinstance(translator, GoogleCloudTranslator):
    st.warning(
        "Not using the official Google Cloud Translation API. Add "
        "`GOOGLE_TRANSLATE_API_KEY` to `.streamlit/secrets.toml` and restart the app to use it.",
        icon="🔑",
    )

col_src, col_swap, col_tgt = st.columns([5, 1, 5], vertical_alignment="bottom")
with col_src:
    st.selectbox("From", SOURCE_OPTIONS, key="source")
with col_swap:
    st.button("⇄", on_click=swap_languages, help="Swap languages", width="stretch")
with col_tgt:
    st.selectbox("To", TARGET_OPTIONS, key="target")

st.text_area("Text to translate", key="text", height=180, placeholder="Type or paste text here…")

count = len(ss.text)
over = count > MAX_CHARS
st.markdown(
    f'<div class="char-count{" over" if over else ""}">{count:,} / {MAX_CHARS:,} characters</div>',
    unsafe_allow_html=True,
)

if st.button("Translate", type="primary", width="stretch"):
    try:
        with st.spinner("Translating…"):
            translated = translator.translate(
                ss.text, language_code(ss.source), language_code(ss.target)
            )
        detected = CODE_TO_NAME.get(translated.detected_source or "")
        ss.result = {
            "text": translated.text,
            "target": ss.target,
            "via": translated.backend + (f" · detected {detected}" if detected else ""),
            "note": translated.note,
            "audio": None,
        }
    except TranslationError as exc:
        ss.result = None
        st.error(str(exc))
    except Exception as exc:  # anything unexpected from a backend library
        ss.result = None
        st.error(f"Something went wrong while translating: {exc}")

result = ss.result
if result:
    code = LANGUAGES[result["target"]]
    direction = "rtl" if code in RTL_CODES else "ltr"
    st.markdown(
        f'<div class="output-label">Translation · {html.escape(result["target"])}'
        f' · via {html.escape(result["via"])}</div>'
        f'<div class="output-box" dir="{direction}" lang="{code}">{html.escape(result["text"])}</div>',
        unsafe_allow_html=True,
    )
    if result["note"]:
        st.info(result["note"], icon="ℹ️")
    st.write("")

    col_copy, col_listen = st.columns(2)
    with col_copy:
        copy_button(result["text"])
    with col_listen:
        if tts.is_supported(code):
            if st.button("🔊 Listen", width="stretch"):
                try:
                    with st.spinner("Generating audio…"):
                        result["audio"] = speak(result["text"], code)
                except tts.TTSError as exc:
                    st.error(str(exc))
        else:
            st.caption(f"Text-to-speech isn't available for {result['target']}.")

    if result["audio"]:
        st.audio(result["audio"], format="audio/mp3", autoplay=True)
