# Shorts Quality Automation

## Goals

Generated Shorts should be fast enough for the Shorts feed, visually varied, and technically clean before upload.

## Current Defaults

- Script length: 850-1250 characters, hard limit 750-1400 characters.
- TTS speed: 1.06x-1.24x based on original duration.
- Target final narration: 35-82 seconds, with normal output expected around 45-75 seconds.
- Background clips: 2.8-4.2 second deterministic cuts.
- Final video: 1080x1920, 30 fps, H.264 CRF 19, `+faststart`.
- Final audio: AAC, 48 kHz, stereo, 128k target, loudness normalized to `I=-16:TP=-1.5:LRA=11`.

## Story-Aware Backgrounds

GPT now returns `visual_keywords` with each script. The render stage uses those keywords before fallback queries so stock footage better matches the story, for example:

- `phone texting`
- `couple argument`
- `apartment hallway`
- `coffee shop`
- `person thinking`

Generic queries like `nature`, `background`, and `landscape` are ignored unless explicitly reintroduced in code.

## Pixabay Filtering

Pixabay results are filtered to avoid low-quality Shorts visuals such as:

- green screen / chroma key
- abstract backgrounds
- cartoon / anime / animation
- game / gaming / logo / VFX

## Quality Gate

Rendered MP4 files still must pass the hard upload validation: video stream, audio stream, duration, size, and resolution.

The render stage also logs non-blocking quality warnings when output is outside recommended Shorts thresholds:

- duration above `MAX_RECOMMENDED_SHORTS_DURATION_SECONDS` (default: 85)
- FPS below `MIN_RECOMMENDED_RENDER_FPS` (default: 29)
- video bitrate below `MIN_RECOMMENDED_VIDEO_BITRATE` (default: 3000000)
- audio sample rate below `MIN_RECOMMENDED_AUDIO_SAMPLE_RATE` (default: 44100)
- audio bitrate below `MIN_RECOMMENDED_AUDIO_BITRATE` (default: 96000)

## Tunable Environment Variables

- `TTS_BASE_SPEED`
- `TTS_SHORT_SPEED`
- `TTS_MEDIUM_SPEED`
- `TTS_LONG_SPEED`
- `TTS_VERY_LONG_SPEED`
- `TTS_MAX_SPEED`
- `TTS_MIN_FINAL_SECONDS`
- `TTS_MAX_FINAL_SECONDS`
- `SHORTS_BG_MIN_CLIP_SECONDS`
- `SHORTS_BG_MAX_CLIP_SECONDS`
- `SHORTS_RENDER_FPS`
- `FINAL_RENDER_CRF`
- `FINAL_AUDIO_BITRATE`
- `FINAL_AUDIO_LOUDNORM`
- `PIXABAY_MAX_QUERIES_PER_ITEM`
