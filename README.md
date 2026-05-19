# NASA APOD Bot

A Telegram bot that shares [NASA's Astronomy Picture of the Day](https://apod.nasa.gov) daily at 08:00 UTC and answers user questions about the current APOD using AI.

## Features

- **Daily APOD** — Fetches and broadcasts today's APOD to all subscribers every day at 08:00 UTC
- **AI Q&A** — Uses DeepSeek to answer questions about the current APOD in 2–3 concise sentences
- **Voice Narration** — Generates an audio voice note of the APOD explanation using Kokoro TTS (female voice) and sends it alongside the image
- **Smart Captions** — Fits the full APOD explanation in the image caption when under Telegram's 1024-character limit; appends the complete text below when it exceeds
- **Vision-Aware** — Answers visual questions (colors, structure, composition) using the APOD's detailed description text
- **Clean /start** — Welcomes new users with a text message followed by today's APOD image and caption
- **Off-Topic Guard** — Politely rejects unrelated questions with a fixed response

## How It Works

1. At 08:00 UTC daily, the bot fetches the latest APOD from NASA and broadcasts it to all known subscribers
2. Each APOD delivery includes: image → text explanation (if needed) → voice note narration
3. Users can ask the bot questions — it passes the APOD metadata + description to DeepSeek and returns a concise answer
4. Non-APOD questions receive: *"I only specialize in Astronomy Picture of The Day by NASA"*

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + today's APOD |
| `/apod` | Show today's APOD |
| `/about` | Bot information |
| Any APOD-related question | AI-powered answer |

## Architecture

```
/root/nasa-apod-bot/
├── bot.py              # Main bot (polling, scheduling, Q&A, broadcasting, TTS)
├── nasa-apod.service   # systemd service for auto-start and restart
├── .env.example        # Environment variable template
├── .venv/              # Python virtual environment
├── kokoro-v1.0.onnx    # Kokoro TTS model (not in git — download separately)
├── voices-v1.0.bin     # Kokoro voice pack (not in git — download separately)
├── apod_chats.json     # Known chat IDs for daily broadcast (runtime)
└── bot.log             # Runtime logs
```

## Dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install kokoro-tts requests
sudo apt-get install ffmpeg
```

### Download Kokoro TTS Model Files

```bash
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin
```

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/cykosi/nasa-apod-bot.git /root/nasa-apod-bot
```

### 2. Configure credentials

Edit `bot.py` and set the following constants:

```python
TELEGRAM_TOKEN = "your-telegram-bot-token"
NASA_API_KEY = "your-nasa-api-key"
DEEPSEEK_API_KEY = "your-deepseek-api-key"
```

### 3. Run

```bash
/root/nasa-apod-bot/.venv/bin/python3 /root/nasa-apod-bot/bot.py
```

### 4. (Optional) Install as a systemd service

```bash
sudo cp /root/nasa-apod-bot/nasa-apod.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nasa-apod
sudo systemctl start nasa-apod
```

## APIs Used

| Service | Purpose | Endpoint |
|---------|---------|----------|
| Telegram Bot API | Message delivery & user interaction | `api.telegram.org` |
| NASA APOD | Astronomy Picture of the Day | `api.nasa.gov/planetary/apod` |
| DeepSeek | AI-powered Q&A | `api.deepseek.com/v1/chat/completions` |
| Kokoro TTS | Voice narration of APOD explanations | Local ONNX model |

## License

MIT
