# AWS Terraform 설계

## 현재 적용 구조

```text
Artifact bucket: youtube-shorts-automation-160885253413-apne2
Terraform state bucket: ytshorts-terraform-state-160885253413-apne2
Terraform lock table: ytshorts-terraform-locks
DynamoDB content table: ytshorts-content
ECR repository: ytshorts-app
CodeBuild project: ytshorts-image-build
Batch compute environment: ytshorts-fargate
Batch queue: ytshorts-pipeline
Batch job definitions: ytshorts-stage, ytshorts-script, ytshorts-render
Step Functions state machine: ytshorts-pipeline
Planner Lambda: ytshorts-planner
Publisher Lambda: ytshorts-publisher
Budget/alert Lambda: ytshorts-budget-notifier
Generate schedule: ytshorts-generate-refill
Upload schedule: ytshorts-upload-daily
```

기존 EC2 launcher, CloudWatch Event Rule, instance profile, userdata SSM parameter는 제거했습니다.

## 생성 리소스

- S3 bucket: 산출물, source bundle, 상태 파일 저장
- DynamoDB: 콘텐츠 상태, 예약 시각, 업로드 결과 저장
- ECR: Batch 컨테이너 이미지 저장
- CodeBuild: S3 source bundle을 Docker image로 빌드 후 ECR push
- AWS Batch/Fargate: 생성 단계별 컨테이너 실행, 렌더링은 array job으로 병렬화
- Step Functions: planner 결과에 따라 생성/업로드 workflow 오케스트레이션
- EventBridge Scheduler: 월 2회 재고 보충, 일간 업로드 트리거
- Lambda: publish-ready 부족분 계산, YouTube upload API 호출
- SNS/Budgets/EventBridge: 비용 예산, Step Functions 실패, Batch 실패 Slack 알림
- IAM: Batch, CodeBuild, Step Functions, Scheduler, Publisher 권한

## Schedules

```text
ytshorts-generate-refill: cron(0 2 1,15 * ? *) Asia/Seoul
ytshorts-upload-daily:  cron(0 8 * * ? *) Asia/Seoul
```

생성 workflow는 먼저 `ytshorts-planner` Lambda를 호출합니다. `days`는 “무조건 생성 개수”가 아니라 목표 재고일수입니다. planner는 현재 publish-ready 미업로드 재고를 세고 `days + buffer_days`에 모자란 만큼만 `needed_new_items`로 반환합니다. `needed_new_items=0`이면 Step Functions는 Batch/Fargate를 시작하지 않고 성공 종료합니다.

기본값은 목표 14일, 버퍼 3일, 신규 생성 cap 21개입니다. Reddit 후보 수집량은 생성 일수와 분리해 `reddit_max_posts=60`, `reddit_min_needed=30`을 기본값으로 둡니다.

렌더링은 `needed_new_items=1`이면 단건 Batch job으로 실행하고, 2개 이상이면 같은 수량의 Batch array job으로 실행합니다. 이전처럼 고정 array size를 사용하지 않으므로 부족분이 적을 때 빈 render shard 비용이 발생하지 않습니다.

14개를 요청해도 모든 단계가 14개를 보장하지는 않습니다. Reddit 후보 부족, GPT 검증 실패, Polly/TTS 길이 검증 실패, Pixabay/ffmpeg 렌더 실패가 있으면 실제 publish-ready 개수는 줄 수 있습니다. 버퍼는 이 실패분을 흡수하기 위한 것이고, 다음 refill에서 부족분을 다시 채웁니다.

## 시크릿 관리

시크릿 값은 Terraform 변수로 받지 않습니다. Terraform state에 민감 값이 남지 않도록 `/ytshorts/*` SSM SecureString을 런타임에 읽거나 컨테이너 secret으로 주입합니다.

필수 파라미터:

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

S3와 Polly는 전용 정적 access key를 사용하지 않습니다. Batch/Lambda runtime은 IAM role과 AWS SDK 기본 credential chain을 사용합니다. 외부에 노출된 기존 정적 키는 각 발급처에서 폐기/재발급해야 합니다.

정리된 legacy SSM 파라미터: 전용 S3 access key, 전용 Polly access key, YouTube API key, Google credential path. Polly 전용 IAM access key는 비활성화했고, S3 전용 access key는 현재 권한으로 비활성화가 거부되어 SSM에서는 제거했습니다.

선택 파라미터:

```text
/ytshorts/REDDIT_CLIENT_ID
/ytshorts/REDDIT_CLIENT_SECRET
/ytshorts/REDDIT_USER_AGENT
```

YouTube OAuth client/refresh token은 현재 SSM SecureString에 저장되어 있습니다. 값이 `PENDING`이면 publisher Lambda는 업로드를 시도하지 않고 `UPLOAD_BLOCKED` 상태를 남기도록 구현되어 있습니다.

YouTube OAuth scope는 업로드 전용 `youtube.upload`와 상태 조회/삭제가 가능한 `youtube.force-ssl`을 함께 요청합니다. 기존 refresh token이 업로드 전용 scope로 발급된 경우 새 scope는 자동으로 소급 적용되지 않으므로, 처리 상태 조회나 삭제가 필요한 경우 `scripts/youtube_oauth_setup.py`로 refresh token을 다시 발급해 SSM에 저장해야 합니다.

## Source Bundle Build

CodeBuild source는 private GitHub 연결 없이 동작하도록 S3 bundle을 사용합니다.

```bash
mkdir -p infra/terraform/.build
git archive --format=zip -o infra/terraform/.build/source.zip HEAD
aws s3 cp infra/terraform/.build/source.zip \
  s3://youtube-shorts-automation-160885253413-apne2/source/source.zip
aws codebuild start-build --project-name ytshorts-image-build
```

## Terraform 명령

```bash
cd infra/bootstrap
terraform init
terraform apply

cd ../terraform
terraform init
terraform plan
terraform apply
```
