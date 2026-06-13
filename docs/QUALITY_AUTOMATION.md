# Shorts Quality Automation

## Goals

Generated Shorts should be fast enough for the Shorts feed, visually varied, and technically clean before upload.

## Current Defaults

- Script length: 780-1080 preferred characters, hard limit 750-1150 characters.
- Script model: `gpt-5.5` by default for source-faithful adaptation quality.
- Filter model: `gpt-5.4-nano` by default for low-cost viability classification.
- TTS speed: 1.06x-1.24x based on original duration.
- Target final narration: 35-82 seconds, with normal output expected around 45-75 seconds.
- Background clips: 3.4-5.6 second deterministic cuts by default, expanded to 4.0-6.6 seconds for longer narration.
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

When story-specific clips are not enough, the Pixabay search falls back to muted ASMR-style visual queries such as:

- `hands typing keyboard close up`
- `phone screen close up`
- `writing notebook close up`
- `coffee pouring close up`
- `rain window`
- `candle flame close up`

These are visual-only background clips. Their audio is removed before final rendering so they do not compete with narration.

Pixabay candidates are sorted and filtered before download:

- query/tag overlap is preferred
- concrete tags such as phone, hands, typing, coffee, hallway, office, or conversation are preferred
- higher-resolution Pixabay variants are selected by long edge, not by label order
- generic low-signal landscape, sky, drone, and sunset results are rejected unless they match the query
- green screen, abstract, template, animation, game, logo, VFX, slideshow, intro, and outro clips are rejected

If fresh Pixabay IDs are exhausted, the renderer can reuse older IDs as a fallback (`PIXABAY_ALLOW_USED_ID_FALLBACK=1`) rather than failing a whole batch because of inventory scarcity.

## Script Quality Gate

Script generation uses Structured Outputs with these required fields:

- `source_summary`: concise summary of the original conflict
- `story_beats`: 4-7 source-grounded beats
- `adaptation_strategy`: what was compressed or plausibly dramatized
- `retention_angle`: why the story should hold viewers after the opening hook
- `viewer_question`: the final comment prompt
- `marketability_score`: model self-audit from 1 to 5
- `visual_keywords`: 5-8 concrete stock-video search phrases
- `script`: first-person narration paragraphs

The source is treated as a seed story rather than a transcript. The writer may compress repeated events, add plausible small dialogue, sharpen stakes, and choose a more relatable angle, but must keep the same core conflict, relationship type, narrator action, consequence, and final moral question. The writer must not invent new crimes, lawsuits, police, violence, sexual content, cheating, medical emergencies, revenge plans, pregnancy, minors, or job loss unless the source clearly supports them.

The local validator rejects scripts before TTS when any hard failure is detected:

- source content is too thin or likely truncated
- source involves minors or teen/high-school context in romantic or sexual conflict
- script is outside the 750-1150 character hard bounds
- first sentence hook is missing, too long, starts with slow setup, or lacks a concrete crossed line
- final beats do not include a direct engagement question
- narration contains meta language such as JSON/script/AI references
- source summary, story beats, adaptation strategy, retention angle, viewer question, or marketability score are missing or weak
- visual keywords are too sparse after cleanup
- script invents unsupported high-stakes facts such as police/legal threats, violence, cheating, pregnancy, medical emergencies, minors, or job loss
- lexical overlap with the original story is too low, which usually means the adaptation drifted from the source

Non-blocking warnings are stored in `quality_warnings` for valid scripts that are outside the preferred 780-1080 character target or show repetitive paragraph starts.

## YouTube Metadata Style

Upload metadata follows the current reference Shorts pattern:

- title starts with a concrete conflict sentence and ends with `#shorts #story #reddit #viral`
- title is capped at YouTube's 100-character limit after hashtag packaging
- description contains the generated description, the sharper viewer question, and the same hashtag line
- tags preserve source-specific tags first, then add stable discovery tags such as `shorts`, `story`, `reddit`, `viral`, `storytime`, and `reddit story`

GPT is instructed not to add hashtags directly. The local metadata post-processor applies the channel style only after script quality validation succeeds.

## Source Integrity

Reddit collection stores source diagnostics with each raw post:

- `content_char_count`
- `content_word_count`
- `content_hash`
- `source_is_truncated`
- `source_truncation_reason`
- `source_detail_checked`
- `source_detail_improved`

The Reddit API collector re-checks accepted posts through the post detail endpoint by default (`REDDIT_FETCH_POST_DETAILS=1`) and keeps the longer body when the detail response improves the listing body. PullPush fallback uses `selftext`, then `body`, then `text` so archived bodies are not missed.

## Pixabay Filtering

Pixabay results are filtered to avoid low-quality Shorts visuals such as:

- green screen / chroma key
- abstract backgrounds
- cartoon / anime / animation
- game / gaming / logo / VFX

Source video selection defaults to 1080p-or-better material for Shorts framing:

- `PIXABAY_MIN_SOURCE_LONG_EDGE=1920`
- `PIXABAY_MIN_SOURCE_SHORT_EDGE=1080`

This avoids common 1280x720 sources being upscaled to 1080x1920, which usually produces soft, low-quality background footage.

Downloaded Pixabay candidates also pass a lightweight sharpness gate before they can be used in a background sequence. FFmpeg samples a few frames, Pillow/numpy compute a Laplacian-variance sharpness score, and candidates below `PIXABAY_MIN_SHARPNESS_SCORE` are discarded. This catches high-resolution but visually blurred source clips without adding OpenCV or another heavy runtime dependency.

Final rendering normalizes the video to 1080x1920 with Lanczos scaling, burns ASS captions after normalization, renders captions over a 4:4:4 intermediate frame, then converts to YouTube-compatible yuv420p at the end. This keeps caption edges sharper than burning text into an already subsampled frame.

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
- `PIXABAY_ENABLE_ASMR_FALLBACK`
- `PIXABAY_ASMR_FALLBACK_QUERIES`
- `PIXABAY_ALLOW_USED_ID_FALLBACK`
- `PIXABAY_PRIMARY_FALLBACK_QUERIES`
- `PIXABAY_MAX_PAGES_PER_QUERY`
- `PIXABAY_MIN_DOWNLOAD_BYTES`
- `PIXABAY_MIN_SOURCE_LONG_EDGE`
- `PIXABAY_MIN_SOURCE_SHORT_EDGE`
- `PIXABAY_ALLOW_LOW_RES_FALLBACK`
- `PIXABAY_ENABLE_SHARPNESS_FILTER`
- `PIXABAY_MIN_SHARPNESS_SCORE`
- `PIXABAY_SHARPNESS_SAMPLE_FRAMES`
- `PIXABAY_SHARPNESS_SAMPLE_INTERVAL`
- `PIXABAY_SHARPNESS_SAMPLE_WIDTH`
- `SHORTS_RENDER_FPS`
- `SHORTS_SCALE_FILTER`
- `BG_SEGMENT_CRF`
- `BG_SEGMENT_PRESET`
- `FINAL_RENDER_CRF`
- `FINAL_RENDER_PRESET`
- `FINAL_AUDIO_BITRATE`
- `FINAL_AUDIO_LOUDNORM`
- `PIXABAY_MAX_QUERIES_PER_ITEM`
- `REDDIT_FETCH_POST_DETAILS`
- `REDDIT_DETAIL_REQUEST_DELAY_SECONDS`
- `FILTER_REASONING_EFFORT`
- `FILTER_MODEL`
- `SCRIPT_MODEL`
