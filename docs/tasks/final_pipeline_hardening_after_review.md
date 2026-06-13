# Final Pipeline Hardening After Review

## Summary

The pipeline now blocks bad, stale, legacy, or partially generated content at the upload boundary and at each artifact boundary. Metadata quality is not enough by itself: TTS audio, speech marks, aligned subtitles, render results, and upload metadata must all exist and pass deterministic gates before the next stage runs.

## Implemented Checklist

- Upload scheduler runs `content_gate` at upload time and scans due items in scheduled order.
- Failed upload candidates are marked `REJECTED_BY_CONTENT_GATE` and skipped without upload.
- Production upload scheduler does not use legacy metadata unless `ALLOW_LEGACY_UPLOAD_METADATA=1`.
- TTS stage rewrites `final_metadata.json` to successful TTS items only.
- TTS success metadata records voice, WPM, original duration, and estimated final duration.
- Failed TTS items are written to `failed_posts.json`.
- Subtitle generation aligns `caption_chunks` to Polly word marks and writes SRT entries from chunks.
- Caption alignment fallback is marked explicitly and production fallback can be blocked.
- Real Reddit/PullPush metadata requires source context in production unless explicitly overridden.
- Unknown source providers are blocked in production unless explicitly overridden.
- Stage artifact gates remove items missing mp3, marks, SRT, TTS check rows, final MP4, or video keys.
- Title quality validation covers broader real conflict verbs and objects.
- Caption retention checks reject generic, overlong, or weak first-caption chunks.

## Verification

Run:

```bash
pytest -q
terraform fmt -check -recursive infra/terraform
python3 -m compileall generator shared infra/terraform/lambda
git diff --check
```
