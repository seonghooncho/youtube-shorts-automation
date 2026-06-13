# Hardening After Code Review

## Summary

The generation pipeline now prefers skipping weak inventory over filling the queue with synthetic, local-template, generic, or unsafe content. Collection, filtering, script acceptance, TTS, render/finalize, publish-ready preparation, and publisher upload all have deterministic safety checks before work continues.

## Implemented Checklist

- Synthetic Reddit source fallback is disabled by default.
- Production collection ignores synthetic fallback unless `ALLOW_SYNTHETIC_IN_PRODUCTION=1`.
- Filter local fallback is disabled by default with `FILTER_LOCAL_FALLBACK_ENABLED=0`.
- Local source scorecard no longer gives synthetic sources a score bonus.
- A centralized `generator/text/content_gate.py` blocks unsafe final metadata before downstream stages.
- TTS uses `tts_text` after normalizing `voiceover_lines`, `script`, and `caption_chunks`.
- Script schema, prompt example, regeneration text, and validator use the same 7 to 10 line standard.
- The script system prompt now carries the non-negotiable native-English, concrete-detail, anti-template rules.
- The native-viewer critic receives compact source/draft JSON instead of the full generation prompt.
- Predicted performance fields are generated and checked by the final gate.
- Batch generation can finish with fewer accepted items and logs desired, accepted, and rejected counts.
- Source authenticity fields are propagated into final metadata.
- Public title validation blocks AITA framing, `#viral`, generic titles, dangling titles, and titles without concrete actors/actions.
- Publisher Lambda repeats the critical upload safety checks.

## Gate Policy

The content gate hard-fails metadata that has synthetic sources, local-template fallback, missing real source URLs, missing narration, missing public upload fields, weak critic/predicted scores, bad public titles, unsafe script quality issues, or caption chunks that violate the current Shorts caption constraints.

## Verification

Relevant regression coverage now includes:

- Synthetic fallback default disabled and production guard.
- Filter fallback default disabled and LLM quota skip behavior.
- Synthetic/local-template rejection by the centralized gate.
- Bad metadata blocked before TTS and publish-ready finalization.
- Prompt/example/schema line-count consistency.
- Narration field derivation from legacy `script`.
- Critic and predicted performance threshold failures.
- Title quality validation and `#viral` removal.
- Batch diversity duplicate rejection.

Run:

```bash
pytest -q
```
