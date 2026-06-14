from pathlib import Path

from shared.jobs import runner


ROOT = Path(__file__).resolve().parents[1]


def test_mode_generate_uses_staged_pipeline(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "run_generate_stage", calls.append)
    monkeypatch.setattr(runner, "send_slack_message", lambda _message: None)

    runner._do_generate()

    assert calls == ["collect", "filter", "script", "tts", "subtitles", "render", "finalize"]


def test_terraform_upload_workflow_uses_batch_scheduler():
    main_tf = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")
    publish_ready = main_tf.split("PublishReady = {", 1)[1].split("}", 1)[0]

    assert 'Resource   = "arn:aws:states:::batch:submitJob.sync"' in publish_ready
    assert '{ Name = "MODE", Value = "upload" }' in main_tf
    assert "FunctionName = aws_lambda_function.publisher.arn" not in publish_ready


def test_terraform_runtime_config_enforces_production_and_cost_controls():
    main_tf = (ROOT / "infra/terraform/main.tf").read_text(encoding="utf-8")

    for expected in (
        'APP_ENV                                       = "production"',
        'YT_ENV                                        = "production"',
        'TARGET_ACCEPTED_SCRIPTS                       = "2"',
        'CANDIDATE_BACKUP_ACCEPT_SCORE                 = "70"',
        'SOURCE_LLM_EVAL_LIMIT                         = "10"',
        'SOURCE_LLM_EVAL_LIMIT_MAX                     = "12"',
        'SCRIPT_SOURCE_DRAFT_LIMIT                     = "10"',
        'SCRIPT_NEAR_MISS_REWRITE_LIMIT                = "1"',
        'SCRIPT_STOP_AFTER_ACCEPTED_TARGET             = "0"',
        'SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE              = "1"',
        'SCRIPT_ALLOW_LLM_REWRITE_ON_NARRATIVE_FAILURE = "0"',
        'SCRIPT_ENABLE_JSON_FALLBACK                   = "0"',
        'SCRIPT_MAX_STRUCTURED_ATTEMPTS                = "1"',
        'FILTER_LOCAL_FALLBACK_ENABLED                 = "0"',
        'SCRIPT_LOCAL_FALLBACK_ENABLED                 = "0"',
        'TOKEN_OVERHEAD_TARGET_RATE                    = "0.10"',
        'TTS_ACCEPT_NEAR_MISS_PACING                   = "1"',
        'TTS_HARD_MIN_FINAL_SECONDS                    = "28"',
        'TTS_HARD_MAX_FINAL_SECONDS                    = "80"',
        'TTS_HARD_MAX_WPM                              = "245"',
        'TTS_ALLOW_LLM_REGENERATE                      = "0"',
        'CAPTION_RENDER_MODE                           = "chunk"',
        'OPENING_SILENCE_SECONDS                       = "0.25"',
    ):
        assert expected in main_tf

    for secret_name in (
        '"YOUTUBE_CLIENT_ID"',
        '"YOUTUBE_CLIENT_SECRET"',
        '"YOUTUBE_REFRESH_TOKEN"',
        '"YOUTUBE_TOKEN_URI"',
    ):
        assert secret_name in main_tf
