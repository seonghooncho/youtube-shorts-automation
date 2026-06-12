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

`days`는 목표 재고일수입니다. workflow는 현재 publish-ready 미업로드 재고를 보고 `days + buffer`에 부족한 만큼만 생성합니다. 생성 smoke는 같은 workflow에 `{"mode":"generate","days":1}`을 전달할 수 있지만, 이미 충분한 publish-ready 재고가 있으면 collect 이후 단계가 no-op으로 종료될 수 있습니다. 후보 수집량은 Terraform의 `reddit_max_posts`, `reddit_min_needed`로 별도 관리됩니다.

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
- Publisher Lambda logs: CloudWatch Logs `/aws/lambda/ytshorts-publisher`
- Budget/alert Lambda logs: CloudWatch Logs `/aws/lambda/ytshorts-budget-notifier`
- Step Functions executions: `ytshorts-pipeline`
- 생성 상태: S3 `raw/`, `scripts/`, `audio/`, `videos/`, `publish-ready/`, `state/`
- 콘텐츠 상태: DynamoDB `ytshorts-content`
- 비용/실패 알림: SNS `ytshorts-alerts` -> Lambda `ytshorts-budget-notifier` -> Slack

## 업로드 안전장치

기본 업로드는 `private`입니다. 공개 업로드는 Terraform variable 또는 Lambda/Batch environment의 `YOUTUBE_PRIVACY_STATUS=public`로 명시합니다.

YouTube upload에는 API key가 아니라 OAuth refresh token이 필요합니다. OAuth 값은 `/ytshorts/YOUTUBE_CLIENT_ID`, `/ytshorts/YOUTUBE_CLIENT_SECRET`, `/ytshorts/YOUTUBE_REFRESH_TOKEN`, `/ytshorts/YOUTUBE_TOKEN_URI`에 저장되어 있습니다. 해당 값이 `PENDING`이면 업로드 workflow는 안전하게 blocked 상태로 종료합니다.

publisher Lambda는 `PUBLISH_REBASE_STALE_DAYS`보다 오래 밀린 미업로드 큐를 발견하면 예약일을 현재 시점부터 다시 일별 슬롯으로 정렬합니다. 오래된 예약일이 계속 누적되어 과거 스케줄만 업로드되는 상황을 줄이기 위한 보정입니다.
