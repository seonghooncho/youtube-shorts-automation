# runner.py
import os
from shared.utils.slack_notify import send_slack_message

# 업로드
try:
    from shared.jobs.upload_scheduler import upload_batch_pipeline
except Exception:
    upload_batch_pipeline = None

try:
    from shared.jobs.staged_generate import run_generate_stage
except Exception:
    run_generate_stage = None


def _do_generate():
    if not run_generate_stage:
        raise RuntimeError("run_generate_stage() 로더 실패: shared.jobs.staged_generate 확인 필요")
    stages = ["collect", "filter", "script", "tts", "subtitles", "render", "finalize"]
    send_slack_message("🎬 staged 배치 생성 파이프라인 시작")
    for stage_name in stages:
        send_slack_message(f"🎬 생성 스테이지 시작: {stage_name}")
        run_generate_stage(stage_name)
        send_slack_message(f"✅ 생성 스테이지 종료: {stage_name}")
    send_slack_message("✅ staged 배치 생성 파이프라인 종료")


def _do_upload():
    if not upload_batch_pipeline:
        raise RuntimeError("upload_batch_pipeline() 로더 실패: shared.jobs.upload_scheduler 확인 필요")
    send_slack_message("📤 업로드 파이프라인 시작")
    upload_batch_pipeline()
    send_slack_message("🏁 업로드 파이프라인 종료")


if __name__ == "__main__":
    # 우선순위: CLI 인자보다 환경변수 MODE 사용(깃액션에서 세팅)
    mode = os.getenv("MODE", "upload").lower().strip()
    stage = os.getenv("STAGE", "").lower().strip()

    try:
        if stage:
            if not run_generate_stage:
                raise RuntimeError("run_generate_stage() 로더 실패: shared.jobs.staged_generate 확인 필요")
            send_slack_message(f"🎬 생성 스테이지 시작: {stage}")
            run_generate_stage(stage)
            send_slack_message(f"✅ 생성 스테이지 종료: {stage}")

        elif mode == "generate":
            _do_generate()

        elif mode == "upload":
            _do_upload()

        elif mode in ("both", "all"):
            # 생성 후 업로드까지 한 번에
            _do_generate()
            _do_upload()

        else:
            send_slack_message(f"❓ 알 수 없는 MODE: {mode}")
    except Exception as e:
        send_slack_message(f"💥 런너 실패: {e}")
        raise
