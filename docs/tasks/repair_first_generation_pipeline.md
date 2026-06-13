# Repair-First Script Generation Pipeline

This task moves script generation toward a repair-first flow.

LLM output should focus on the story narration. Deterministic code should repair mechanical metadata before another LLM rewrite is attempted:

- `public_title`
- `first_frame_text`
- `opening_visual_query`
- `visual_beat_queries`
- `caption_chunks`
- `tts_text`
- `retention_angle`
- `cut_plan`
- near-minimum script length
- final viewer question placement

Acceptance target:

- usable short drafts around 540-649 chars receive one source-grounded repair line before the final question
- captions are rebuilt from `voiceover_lines`
- title/opening visual/first frame are rebuilt deterministically
- mechanical failures do not trigger full LLM regeneration
- per-source LLM drafts default to 2 total attempts
- source filter is stricter and gate-aware
- generation summary includes attempts, repairs, accepts, rejects, and top failure codes
