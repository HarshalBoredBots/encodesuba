![EncodeBot Banner](https://i.ibb.co/Lz3H4kZ6/start.png)

# EncodeBot

> A Telegram-based video encoding assistant built with Pyrogram and FFmpeg.

EncodeBot is designed for handling video workflows directly inside Telegram. The project focuses heavily on FFmpeg-based processing, including multi-resolution encoding, watermark rendering, metadata injection, and automated batch handling.

---

## Commands

| Command   | Purpose                                                                                                                                                                                                                                             |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/es`     | Opens the settings panel where users configure output resolutions, CRF values, codec, audio bitrate, watermark, metadata, filename template, upload mode, and thumbnail. All settings are saved per user and reused automatically for future tasks. |
| `/encode` | Encodes a single replied video into one or more selected resolutions. The command applies the saved FFmpeg profile for each resolution, including codec, CRF, audio bitrate, metadata, and watermark settings.                                      |
| `/be`     | Batch-encodes an entire Telegram album. Each file is processed using the same FFmpeg profile and naming template, making it useful for encoding complete series episodes or multi-part uploads.                                                     |
| `/rename` | Renames a single video without changing the selected resolution. It can still inject metadata and apply watermarking if enabled, allowing files to be reorganized without a full encode.                                                            |
| `/br`     | Batch-renames a Telegram album using the saved filename template. Useful when preparing complete seasons or collections with consistent naming and optional watermarking.                                                                           |
| `/mi`     | Generates a MediaInfo report from a replied file or URL. The command extracts technical details such as codec, bitrate, duration, audio tracks, subtitle tracks, and container information.                                                         |
| `/status` | Displays the active encoding queue, current FFmpeg stage, system resource usage, and queued tasks. It helps track which file is downloading, encoding, or uploading in real time.                                                                   |
| `/cancel` | Stops a running or queued task instantly. The bot terminates the FFmpeg process, removes temporary files, and continues with the next task in the queue.                                                                                            |

---

## FFmpeg Processing

EncodeBot is centered around FFmpeg. Every encoding task builds a dynamic FFmpeg command based on the user's selected settings.

For each selected resolution, FFmpeg:

* Rescales the video while preserving aspect ratio
* Applies the selected codec (`libx264` or `libx265`)
* Uses the configured CRF and preset values
* Re-encodes or copies the audio stream
* Injects metadata and optional thumbnail
* Burns a watermark into the video when enabled

The project mainly focuses on understanding how FFmpeg filters and encoder settings affect quality, size, and speed.

---

## Encoding Concepts

### CRF

CRF (Constant Rate Factor) controls video quality.

* Lower CRF = higher quality and larger file size
* Higher CRF = lower quality and smaller file size

Typical values:

| CRF   | Result                      |
| ----- | --------------------------- |
| 18–20 | Visually lossless           |
| 21–24 | High quality                |
| 25–30 | Smaller size, lower quality |

---

### Codec

EncodeBot supports:

* `libx264` — Faster encoding and better compatibility
* `libx265` — Better compression and smaller files, but slower

`libx264` is generally used when compatibility matters, while `libx265` is used when smaller output size is preferred.

---

### Audio Encoding

Audio bitrate is configured separately for every resolution.

Common values:

* `96k` for 480p
* `128k` for 720p
* `192k` for 1080p

The audio stream can either be copied directly or re-encoded depending on the selected profile.

---

### Watermark Rendering

The watermark system is implemented using FFmpeg's `drawtext` filter.

Users can provide:

* Text content
* TTF / OTF font file
* Font size and color
* Screen position and padding
* Start and end timing

The watermark is rendered directly onto the video frames during encoding. The project mainly explores how FFmpeg handles custom fonts, positioning, and timed overlays.

---

## Default Encoding Profiles

| Resolution | Codec   | CRF | Audio |
| ---------- | ------- | --- | ----- |
| HDRip      | Copy    | —   | Copy  |
| 1080p      | libx264 | 23  | 192k  |
| 720p       | libx264 | 26  | 128k  |
| 480p       | libx264 | 28  | 96k   |

HDRip mode does not re-encode the source. FFmpeg simply copies the existing video and audio streams while still allowing metadata injection.

---

## Installation

```bash
git clone https://github.com/KunalDahal/Toji-Encode.git
cd Toji-Encode
pip install -r requirements.txt
```

Create a `.env` file:

```env
API_ID=
API_HASH=
BOT_TOKEN=
ALLOWED_GROUP_IDS=
ADMIN_IDS=
```

Run the bot:

```bash
python main.py
```

---

## Requirements

* Python 3.10+
* FFmpeg
* Pyrogram
* TgCrypto
* MTProto / Telegram Bot API access

---

## License

MIT License

## Credits

Built with Pyrogram and FFmpeg by **KunalDahal**