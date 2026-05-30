#!/root/nasa-apod-bot/.venv/bin/python3
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
from datetime import datetime, timezone

import requests

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

KOKORO_MODEL  = os.path.join(PROJECT_DIR, "kokoro-v1.0.onnx")
KOKORO_VOICES = os.path.join(PROJECT_DIR, "voices-v1.0.bin")
KOKORO_VOICE  = "af_nova"
KOKORO_SPEED  = 1.0

_kokoro = None
_kokoro_lock = threading.Lock()


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
    return _kokoro


def generate_audio(text):
    if not os.path.exists(KOKORO_MODEL) or not os.path.exists(KOKORO_VOICES):
        return None
    try:
        import soundfile as sf
        with _kokoro_lock:
            k = _get_kokoro()
        samples, sample_rate = k.create(text, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang="en-us")

        wav_path = os.path.join(tempfile.gettempdir(), f"apod_audio_{os.getpid()}.wav")
        ogg_path = os.path.join(tempfile.gettempdir(), f"apod_audio_{os.getpid()}.ogg")

        sf.write(wav_path, samples, sample_rate)

        subprocess.run([
            "ffmpeg", "-y", "-i", wav_path,
            "-c:a", "libopus", "-b:a", "32k", ogg_path,
        ], capture_output=True, timeout=60)

        os.remove(wav_path)
        return ogg_path
    except Exception as e:
        log(f"TTS error: {e}")
        return None

_ENV_FILE = os.path.join(PROJECT_DIR, ".env")
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                val = val.strip().strip('"').strip("'")
                if key not in os.environ:
                    os.environ[key] = val

def _require(key):
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"FATAL: {key} environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return val

TELEGRAM_TOKEN = _require("TELEGRAM_TOKEN")
NASA_API_KEY   = _require("NASA_API_KEY")
DEEPSEEK_API_KEY = _require("DEEPSEEK_API_KEY")

NASA_APOD_URL  = "https://api.nasa.gov/planetary/apod"
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
DEEPSEEK_API   = "https://api.deepseek.com/v1/chat/completions"
CHATS_FILE     = os.path.join(PROJECT_DIR, "apod_chats.json")

OFF_TOPIC_REPLY = "I only specialize in Astronomy Picture of The Day by NASA"

APOD_KEYWORDS = [
    "apod", "astronomy", "picture", "nasa", "space", "galaxy", "nebula", "star",
    "planet", "moon", "sun", "comet", "asteroid", "telescope", "hubble", "james webb",
    "cosmos", "universe", "supernova", "black hole", "constellation", "today", "image",
    "photo", "explanation", "description", "what is", "tell me about", "explain",
    "who took", "where", "when", "how", "why", "credit", "copyright",
]

today_apod = {}
_apod_lock = threading.Lock()


# ── NASA APOD ────────────────────────────────────────────────────────────────

def fetch_apod():
    global today_apod
    try:
        resp = requests.get(NASA_APOD_URL, params={"api_key": NASA_API_KEY}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        with _apod_lock:
            today_apod = data
        log(f"APOD fetched: {data.get('title', 'Unknown')}")
        return data
    except Exception as e:
        log(f"APOD fetch error: {e}")
        return None


def get_apod():
    with _apod_lock:
        if not today_apod:
            return fetch_apod()
        return today_apod


CAPTION_MAX = 1024  # Telegram photo caption limit

def build_caption(apod, max_chars=CAPTION_MAX):
    title = apod.get("title", "Unknown Title")
    date_str = apod.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    explanation = apod.get("explanation", "")
    credit = apod.get("copyright", "NASA")

    header = f"<b>{title}</b>\n<i>{date_str}</i>\n\n"
    footer = f"\n\nCredit: {credit}"
    available = max_chars - len(header) - len(footer)

    if len(explanation) <= available:
        return header + explanation + footer, explanation

    truncated = explanation[:available - 3] + "…"
    return header + truncated + footer, explanation


def build_short_caption(apod):
    title = apod.get("title", "Unknown Title")
    date_str = apod.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    explanation = apod.get("explanation", "")
    credit = apod.get("copyright", "NASA")
    short = explanation[:300] + ("…" if len(explanation) > 300 else "")
    return (
        f"<b>{title}</b>\n"
        f"<i>{date_str}</i>\n\n"
        f"{short}\n\n"
        f"Credit: {credit}"
    ), explanation

def format_apod_text(apod):
    caption, _ = build_caption(apod)
    return caption


# ── Telegram helpers ─────────────────────────────────────────────────────────

def send_text(chat_id, text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML"
        }, timeout=15)
    except Exception as e:
        log(f"sendText error → {chat_id}: {e}")


def send_voice(chat_id, ogg_path):
    try:
        with open(ogg_path, "rb") as voice:
            resp = requests.post(f"{TELEGRAM_API}/sendVoice", data={
                "chat_id": chat_id,
            }, files={"voice": ("apod.ogg", voice, "audio/ogg")}, timeout=30)
        if not resp.ok:
            log(f"sendVoice FAIL → {chat_id}: {resp.status_code} {resp.text[:200]}")
        else:
            log(f"sendVoice OK → {chat_id}")
    except Exception as e:
        log(f"sendVoice EXC → {chat_id}: {e}")
    finally:
        try:
            os.remove(ogg_path)
        except OSError:
            pass


def send_apod_media(chat_id, apod, caption_prefix=""):
    media_url = apod.get("url", "")
    media_type = apod.get("media_type", "image")
    caption, full_explanation = build_caption(apod)
    truncated = len(caption) >= CAPTION_MAX or caption.endswith("…")
    if caption_prefix:
        caption = caption_prefix + caption

    markup = json.dumps({"keyboard": [["💬Quote"]], "resize_keyboard": True})

    try:
        if media_type == "image":
            resp = requests.post(f"{TELEGRAM_API}/sendPhoto", json={
                "chat_id": chat_id, "photo": media_url, "caption": caption,
                "parse_mode": "HTML", "reply_markup": markup,
            }, timeout=30)
        else:
            text = f"{caption}\n\n<a href=\"{media_url}\">{media_url}</a>"
            resp = requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                "reply_markup": markup,
            }, timeout=30)
        if not resp.ok:
            log(f"sendMedia FAIL → {chat_id}: {resp.status_code} {resp.text[:200]}")
        else:
            log(f"sendMedia OK → {chat_id} ({media_type})")
    except Exception as e:
        log(f"sendMedia EXC → {chat_id}: {e}")

    if truncated:
        send_text(chat_id, full_explanation)

    threading.Thread(target=_send_apod_voice, args=(chat_id, full_explanation), daemon=True).start()


def _send_apod_voice(chat_id, text):
    ogg_path = generate_audio(text)
    if ogg_path:
        send_voice(chat_id, ogg_path)


# ── Chat storage ─────────────────────────────────────────────────────────────

def load_chats():
    try:
        with open(CHATS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_chat(chat_id):
    chats = load_chats()
    if chat_id not in chats:
        chats.append(chat_id)
        with open(CHATS_FILE, "w") as f:
            json.dump(chats, f)


# ── Daily broadcast ──────────────────────────────────────────────────────────

def broadcast_apod():
    apod = fetch_apod()
    if not apod:
        return
    known = load_chats()
    for chat_id in known:
        send_apod_media(chat_id, apod)
    log(f"Broadcast APOD to {len(known)} chats")


def daily_scheduler():
    while True:
        now = datetime.now(timezone.utc)
        next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run = next_run.replace(day=now.day + 1)
        wait = (next_run - now).total_seconds()
        log(f"Next broadcast at {next_run.isoformat()} ({wait:.0f}s)")
        time.sleep(wait)
        log("Broadcasting daily APOD …")
        broadcast_apod()


# ── DeepSeek Q&A ─────────────────────────────────────────────────────────────

def is_apod_related(text):
    text_lower = text.lower()
    return any(kw in text_lower for kw in APOD_KEYWORDS)


def ask_deepseek(question):
    apod = get_apod()
    if not apod:
        return "Unable to fetch today's APOD at the moment. Please try again later."

    system_prompt = (
        "You are a concise astronomy assistant for NASA's Astronomy Picture of the Day. "
        "The APOD data includes a detailed explanation that describes the image and its "
        "astronomical significance. Use this description to answer visual questions about "
        "the image — what's visible, its colors, structure, and composition. Keep responses "
        "to 2-3 sentences maximum. Be accurate and reference the APOD data provided. "
        "If a question is about astronomy but unrelated to today's APOD, you may briefly "
        f"answer if it connects to the topic. Otherwise, respond exactly: '{OFF_TOPIC_REPLY}'"
    )

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Today's APOD data:\n{json.dumps(apod, ensure_ascii=False)}\n\n"
                f"Question: {question}"
            )},
        ],
        "max_tokens": 200,
        "temperature": 0.3,
        "thinking": {"type": "disabled"},
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(DEEPSEEK_API, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]
        result = choice.get("content", "").strip()
        if not result:
            result = choice.get("reasoning_content", "").strip()
        return result or "Sorry, I'm having trouble processing your question right now."
    except Exception as e:
        log(f"DeepSeek error: {e}")
        return "Sorry, I'm having trouble processing your question right now."


def generate_apod_quote(apod):
    title = apod.get("title", "")
    explanation = apod.get("explanation", "")

    system_prompt = (
        "You are a curator of famous quotations. Given an astronomy topic, provide "
        "a relevant, real quotation from a well-known, verified individual (scientist, "
        "philosopher, poet, historical figure, etc.) that relates to the APOD topic. "
        "The quote must be genuine and attributable — do not fabricate. "
        "Respond in exactly this format with no other text:\n"
        '"The quotation text."\n— Author Name, Description (e.g. astronomer, philosopher)'
    )

    payload = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Give me a famous quotation that relates to this APOD:\n"
                f"Title: {title}\nDescription: {explanation}"
            )},
        ],
        "max_tokens": 200,
        "temperature": 0.5,
        "thinking": {"type": "disabled"},
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(DEEPSEEK_API, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]
        result = choice.get("content", "").strip()
        if not result:
            result = choice.get("reasoning_content", "").strip()
        return result or None
    except Exception as e:
        log(f"Quote generation error: {e}")
        return None


# ── Message handler ──────────────────────────────────────────────────────────

def handle_message(chat_id, text):
    save_chat(chat_id)

    if text.startswith("/start"):
        apod = get_apod()
        log(f"/start from {chat_id}, apod={'OK' if apod else 'MISSING'}")
        welcome = (
            "Welcome! I share NASA's Astronomy Picture of the Day daily at 8:00 UTC.\n"
            "Ask me anything about it and I'll answer concisely."
        )
        send_text(chat_id, welcome)
        if apod:
            send_apod_media(chat_id, apod)
        else:
            send_text(chat_id,
                "Unfortunately I couldn't fetch today's APOD right now. Try /apod later."
            )
        return

    if text.startswith("/apod"):
        apod = get_apod()
        if apod:
            send_apod_media(chat_id, apod)
        else:
            send_text(chat_id, "Unable to fetch today's APOD. Please try again later.")
        return

    if text.startswith("💬Quote") or text.lower().startswith("quote"):
        apod = get_apod()
        if apod:
            quote = generate_apod_quote(apod)
            if quote:
                send_text(chat_id, quote)
                log(f"Quote sent → {chat_id}")
            else:
                send_text(chat_id, "Could not generate a quote for this APOD.")
        else:
            send_text(chat_id, "No APOD available. Try /apod first.")
        return

    if text.startswith("/about"):
        send_text(chat_id,
            "I'm an APOD Bot powered by NASA API and DeepSeek LLM. "
            "I share Astronomy Picture of the Day daily and answer questions about it."
        )
        return

    if is_apod_related(text):
        send_text(chat_id, ask_deepseek(text))
    else:
        send_text(chat_id, OFF_TOPIC_REPLY)


# ── Updates polling ──────────────────────────────────────────────────────────

def poll_updates(offset=None):
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as e:
        log(f"Poll error: {e}")
        return []


# ── Main ─────────────────────────────────────────────────────────────────────

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def main():
    fetch_apod()

    threading.Thread(target=daily_scheduler, daemon=True).start()

    offset = None
    while True:
        try:
            updates = poll_updates(offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                log(f"← {chat_id}: {text}")
                handle_message(chat_id, text)
            time.sleep(0.5)
        except Exception as e:
            log(f"CRASH: {e}")
            time.sleep(5)


if __name__ == "__main__":
    log("Starting APOD Bot …")
    while True:
        try:
            main()
        except Exception as e:
            log(f"FATAL: {e}")
            time.sleep(10)
