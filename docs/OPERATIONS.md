# 운영 문서

## 수동 로컬 실행

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-upload-scheduler.txt

STAGE=collect python runner.py
STAGE=filter python runner.py
STAGE=script python runner.py
STAGE=tts python runner.py
STAGE=subtitles python runner.py
STAGE=render python runner.py
STAGE=finalize python runner.py
MODE=upload python runner.py
```

## AWS 수동 트리거

생성 workflow:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:ap-northeast-2:160885253413:stateMachine:ytshorts-pipeline \
  --input '{"mode":"generate","days":14}' \
  --region ap-northeast-2
```

`days`는 목표 재고일수입니다. workflow는 먼저 planner Lambda에서 현재 publish-ready 미업로드 재고를 보고 `days + buffer_days`에 부족한 만큼만 생성합니다. 부족분이 0이면 Batch job을 하나도 시작하지 않고 성공 종료합니다. 생성 smoke는 `{"mode":"generate","days":0,"buffer_days":0,"max_new_items":0}` 입력으로 planner skip path를 확인하는 방식이 가장 저렴합니다. 후보 수집량은 Terraform의 `reddit_max_posts`, `reddit_min_needed`로 별도 관리됩니다.

업로드 workflow:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:ap-northeast-2:160885253413:stateMachine:ytshorts-pipeline \
  --input '{"mode":"upload"}' \
  --region ap-northeast-2
```

업로드 Lambda 단독 smoke:

```bash
aws lambda invoke \
  --function-name ytshorts-publisher \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  /tmp/ytshorts-publisher.json \
  --region ap-northeast-2
```

성과 수집 Lambda 단독 실행:

```bash
aws lambda invoke \
  --function-name ytshorts-metrics-collector \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"metrics"}' \
  /tmp/ytshorts-metrics.json \
  --region ap-northeast-2
```

## 컨테이너 이미지 빌드

```bash
mkdir -p infra/terraform/.build
git archive --format=zip -o infra/terraform/.build/source.zip HEAD
aws s3 cp infra/terraform/.build/source.zip \
  s3://youtube-shorts-automation-160885253413-apne2/source/source.zip \
  --region ap-northeast-2
aws codebuild start-build \
  --project-name ytshorts-image-build \
  --region ap-northeast-2
```

## 로그 위치

- Batch logs: CloudWatch Logs `/aws/batch/ytshorts`
- Planner Lambda logs: CloudWatch Logs `/aws/lambda/ytshorts-planner`
- Publisher Lambda logs: CloudWatch Logs `/aws/lambda/ytshorts-publisher`
- Metrics Lambda logs: CloudWatch Logs `/aws/lambda/ytshorts-metrics-collector`
- Budget/alert Lambda logs: CloudWatch Logs `/aws/lambda/ytshorts-budget-notifier`
- Step Functions executions: `ytshorts-pipeline`
- 생성 상태: S3 `raw/`, `scripts/`, `audio/`, `videos/`, `publish-ready/`, `state/`
- 콘텐츠/소스/성과 상태: DynamoDB `ytshorts-content`
- 비용/실패 알림: SNS `ytshorts-alerts` -> Lambda `ytshorts-budget-notifier` -> Slack

## 업로드 안전장치

기본 업로드는 `public`입니다. 검수용 비공개/일부공개 업로드가 필요하면 Terraform variable 또는 Lambda/Batch environment의 `YOUTUBE_PRIVACY_STATUS=private` 또는 `YOUTUBE_PRIVACY_STATUS=unlisted`로 명시합니다.

YouTube upload에는 API key가 아니라 OAuth refresh token이 필요합니다. OAuth 값은 `/ytshorts/YOUTUBE_CLIENT_ID`, `/ytshorts/YOUTUBE_CLIENT_SECRET`, `/ytshorts/YOUTUBE_REFRESH_TOKEN`, `/ytshorts/YOUTUBE_TOKEN_URI`에 저장되어 있습니다. 해당 값이 `PENDING`이면 업로드 workflow는 안전하게 blocked 상태로 종료합니다.

성과 수집까지 사용하려면 refresh token에 `youtube.readonly`와 `yt-analytics.readonly` scope가 포함되어야 합니다. scope가 부족하면 metrics Lambda는 `/ytshorts/YOUTUBE_API_KEY`로 공개 Data API 통계 조회를 한 번 더 시도합니다. 공개 통계만 수집되면 `METRICS_PARTIAL`, 공개 API에서도 영상이 보이지 않거나 key가 없으면 `METRICS_BLOCKED`와 오류 사유를 남깁니다. Analytics API는 데이터 지연이 있을 수 있으므로 OAuth scope는 충분하지만 행이 없으면 실패가 아니라 `METRICS_PENDING`으로 기록합니다.

publisher Lambda는 `PUBLISH_REBASE_STALE_DAYS`보다 오래 밀린 미업로드 큐를 발견하면 예약일을 현재 시점부터 다시 일별 슬롯으로 정렬합니다. 오래된 예약일이 계속 누적되어 과거 스케줄만 업로드되는 상황을 줄이기 위한 보정입니다.

publisher Lambda는 업로드 직전 `YOUTUBE_MIN_UPLOAD_BYTES`보다 작은 MP4를 `UPLOAD_BLOCKED`로 막습니다. Batch 렌더 단계는 이보다 앞서 `ffprobe`로 최종 MP4의 길이, 해상도, 오디오/비디오 스트림을 검증합니다.

## YouTube 처리중 상태 진단

2026-06-12에 확인한 `Ra2dUfPJmJE` 업로드는 `videos/final/smoke-1781205580.mp4`에서 올라간 2초/9KB smoke MP4였습니다. 파일 자체는 MP4 컨테이너로 열리지만 정상 Shorts 산출물로 보기에는 지나치게 짧고 낮은 비트레이트라 YouTube 처리 지연 또는 실패 상태에 머물 가능성이 큽니다.

SSM에 저장된 refresh token이 `youtube.upload` scope만 갖고 있으면 `videos.list`, `processingDetails`, `videos.delete`, Analytics 조회가 `ACCESS_TOKEN_SCOPE_INSUFFICIENT`로 거부됩니다. 이 scope로는 업로드는 가능하지만 기존 업로드의 처리 상태 확인, 삭제, Analytics 성과 수집은 API로 수행할 수 없습니다. API key fallback은 공개 영상의 기본 통계만 조회할 수 있으므로, 비공개 또는 공개 API에서 보이지 않는 업로드는 여전히 OAuth scope 갱신이 필요합니다.

재발 방지는 코드로 반영되어 있습니다. 같은 유형의 작은 smoke MP4는 이제 publisher에서 업로드 전 차단됩니다. 로컬 uploader는 업로드 전용 refresh token만 있는 경우 `youtube.upload` scope로 fallback해 업로드를 계속 수행합니다. 기존 YouTube 처리중 건을 API로 조회/삭제하거나 성과 수집을 활성화하려면 `scripts/youtube_oauth_setup.py`를 다시 실행해 `youtube.force-ssl`, `youtube.readonly`, `yt-analytics.readonly` scope가 포함된 refresh token을 발급하고 `/ytshorts/YOUTUBE_REFRESH_TOKEN`을 갱신해야 합니다.
