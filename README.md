# YouTube Shorts Automation

Reddit story 기반 YouTube Shorts를 자동으로 생성하고 업로드하는 AWS 배치 파이프라인입니다.

현재 운영 구조는 월 2회 publish-ready 재고를 채우고, 매일 1개씩 YouTube에 public 업로드하는 방식입니다.

## What It Does

```text
Reddit/PullPush source
  -> story filtering
  -> OpenAI script adaptation
  -> AWS Polly TTS
  -> speech-mark subtitles
  -> Pixabay background video selection
  -> FFmpeg Shorts render
  -> S3 publish-ready inventory
  -> YouTube public upload
```

핵심 목표는 사람이 매번 개입하지 않아도 9:16 Shorts 영상을 주기적으로 만들고, 예약된 시간에 YouTube 채널로 올리는 것입니다.

## Current AWS Runtime

| Area | Resource |
| --- | --- |
| Artifact bucket | `youtube-shorts-automation-160885253413-apne2` |
| Terraform state bucket | `ytshorts-terraform-state-160885253413-apne2` |
| Terraform lock table | `ytshorts-terraform-locks` |
| DynamoDB content table | `ytshorts-content` |
| ECR repository | `ytshorts-app` |
| CodeBuild project | `ytshorts-image-build` |
| Batch compute environment | `ytshorts-fargate` |
| Batch queue | `ytshorts-pipeline` |
| Batch job definitions | `ytshorts-stage`, `ytshorts-script`, `ytshorts-render` |
| Step Functions | `ytshorts-pipeline` |
| Planner Lambda | `ytshorts-planner` |
| Publisher Lambda | `ytshorts-publisher` |
| Generate schedule | `ytshorts-generate-refill` |
| Upload schedule | `ytshorts-upload-daily` |

## Schedules

```text
Generate refill: cron(0 2 1,15 * ? *) Asia/Seoul
Daily upload:    cron(0 8 * * ? *) Asia/Seoul
```

Generation is inventory-based, not fixed-count. The planner reads `/ytshorts/GENERATION_BATCH_DAYS`, `/ytshorts/GENERATION_BUFFER_DAYS`, and `/ytshorts/GENERATION_MAX_NEW_ITEMS` from SSM, then calculates:

```text
needed_new_items = min(max_new_items, max(0, batch_days + buffer_days - current_publish_ready_count))
```

Current defaults:

```text
batch_days=14
buffer_days=3
max_new_items=21
```

So the pipeline tries to keep 17 publish-ready videos available. If Reddit candidates, script validation, TTS, Pixabay, or FFmpeg fail, fewer videos may be produced; the next refill run fills the shortfall.

## Where Outputs Go

All generated artifacts are stored in S3:

```text
raw/                  collected Reddit/PullPush data
scripts/              viable posts, generated scripts, metadata, failed posts
audio/mp3/            Polly MP3 files
audio/marks/          Polly speech marks
audio/subtitles/      SRT subtitle files
videos/sources/       merged background video sources
videos/final/         final rendered MP4 files
publish-ready/        upload queue metadata
state/                dedupe and compatibility state
state/render-used-pixabay/
                      array-render Pixabay usage shards
```

DynamoDB `ytshorts-content` keeps content status, scheduled publish time, video key, upload status, and YouTube platform IDs.

## Configuration

Runtime configuration is managed through AWS SSM Parameter Store under `/ytshorts/*`.

Batch jobs receive SSM parameters as container secrets. Planner/publisher Lambda functions keep only `SSM_PARAMETER_PREFIX=/ytshorts` in their environment and read the rest at runtime. EventBridge Scheduler passes only `{"mode":"generate"}` or `{"mode":"upload"}`.

Important runtime config:

```text
/ytshorts/YOUTUBE_PRIVACY_STATUS=public
/ytshorts/GENERATION_BATCH_DAYS=14
/ytshorts/GENERATION_BUFFER_DAYS=3
/ytshorts/GENERATION_MAX_NEW_ITEMS=21
/ytshorts/SCHEDULE_TIMEZONE=Asia/Seoul
/ytshorts/PUBLISH_HOUR_LOCAL=8
/ytshorts/CAPTION_FONT_SIZE=114
/ytshorts/FINAL_RENDER_CRF=17
/ytshorts/PIXABAY_MIN_SOURCE_LONG_EDGE=1920
/ytshorts/PIXABAY_MIN_SOURCE_SHORT_EDGE=1080
/ytshorts/PIXABAY_MIN_SHARPNESS_SCORE=60
```

Required credentials:

```text
/ytshorts/OPENAI_API_KEY
/ytshorts/HF_TOKEN
/ytshorts/PIXABAY_API_KEY
/ytshorts/SLACK_WEBHOOK_URL
/ytshorts/YOUTUBE_CLIENT_ID
/ytshorts/YOUTUBE_CLIENT_SECRET
/ytshorts/YOUTUBE_REFRESH_TOKEN
/ytshorts/YOUTUBE_TOKEN_URI
```

YouTube upload uses OAuth refresh tokens, not a YouTube API key.

## Implementation Notes

Reddit collection does not depend on Selenium DOM scraping. It uses Reddit OAuth/listing APIs when credentials are available, public JSON when possible, and PullPush fallback when Reddit blocks public access.

Rendering is handled by AWS Batch/Fargate, not Lambda. FFmpeg normalizes the final video to 1080x1920, burns centered ASS captions after scaling, renders captions over a 4:4:4 intermediate frame, and emits YouTube-compatible H.264/AAC MP4.

Caption quality defaults:

```text
font: Anton
font size: 114
outline: 7
shadow: 0
fade: 0
center position: 540x960
```

Pixabay selection rejects common low-quality cases:

```text
minimum source long edge: 1920
minimum source short edge: 1080
sharpness gate: Laplacian-variance median score >= 60
blocked tags: green screen, chroma, abstract, animation, game, logo, VFX, slideshow
```

Upload safety:

```text
publisher blocks tiny MP4 files below /ytshorts/YOUTUBE_MIN_UPLOAD_BYTES
render stage validates final MP4 with ffprobe
publisher rebases stale queues so old dates do not permanently clog uploads
```

## Manual Operations

Trigger generation manually:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:ap-northeast-2:160885253413:stateMachine:ytshorts-pipeline \
  --input '{"mode":"generate"}' \
  --region ap-northeast-2
```

Trigger upload manually:

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:ap-northeast-2:160885253413:stateMachine:ytshorts-pipeline \
  --input '{"mode":"upload"}' \
  --region ap-northeast-2
```

Check schedules:

```bash
aws scheduler get-schedule --name ytshorts-generate-refill --region ap-northeast-2
aws scheduler get-schedule --name ytshorts-upload-daily --region ap-northeast-2
```

Build and push the Batch image:

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

## Local Run

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

Local runs need the same required credentials in the environment unless you load them from SSM before execution.

## Terraform

Remote state is stored in S3 with DynamoDB locking.

```bash
cd infra/bootstrap
terraform init
terraform apply

cd ../terraform
terraform init
terraform plan
terraform apply
```

Do not put secret values into Terraform variables. Credential values should be written directly to SSM SecureString parameters.

## Verification

Useful checks:

```bash
pytest -q
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform plan -detailed-exitcode
```

Runtime smoke examples:

```bash
aws lambda invoke \
  --function-name ytshorts-planner \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"generate"}' \
  /tmp/ytshorts-planner.json \
  --region ap-northeast-2
```

Planner should return `needed_new_items` based on SSM-backed inventory settings.

## Docs

- [Product Spec](docs/PRODUCT_SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Infrastructure](docs/INFRASTRUCTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Quality Automation](docs/QUALITY_AUTOMATION.md)
- [Implementation Checklist](docs/IMPLEMENTATION_CHECKLIST.md)
