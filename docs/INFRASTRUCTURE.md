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
Batch job definition: ytshorts-stage
Step Functions state machine: ytshorts-pipeline
Publisher Lambda: ytshorts-publisher
Generate schedule: ytshorts-generate-14day
Upload schedule: ytshorts-upload-daily
```

기존 EC2 launcher, CloudWatch Event Rule, instance profile, userdata SSM parameter는 제거했습니다.

## 생성 리소스

- S3 bucket: 산출물, source bundle, 상태 파일 저장
- DynamoDB: 콘텐츠 상태, 예약 시각, 업로드 결과 저장
- ECR: Batch 컨테이너 이미지 저장
- CodeBuild: S3 source bundle을 Docker image로 빌드 후 ECR push
- AWS Batch/Fargate: 생성 단계별 컨테이너 실행
- Step Functions: 생성/업로드 workflow 오케스트레이션
- EventBridge Scheduler: 주간 생성, 일간 업로드 트리거
- Lambda: YouTube upload API 호출
- IAM: Batch, CodeBuild, Step Functions, Scheduler, Publisher 권한

## Schedules

```text
ytshorts-generate-14day: cron(0 2 ? * MON *) Asia/Seoul
ytshorts-upload-daily:  cron(0 8 * * ? *) Asia/Seoul
```

생성 workflow는 `days` 입력을 `GENERATION_BATCH_DAYS`로 Batch stage에 주입합니다. 운영 스케줄은 14일치를 전달하고, 수동 smoke는 `days: 1`처럼 줄여 실행할 수 있습니다. Reddit 후보 수집량은 생성 일수와 분리해 `reddit_max_posts=30`, `reddit_min_needed=15`를 기본값으로 둡니다.

## 시크릿 관리

시크릿 값은 Terraform 변수로 받지 않습니다. Terraform state에 민감 값이 남지 않도록 `/ytshorts/*` SSM SecureString을 런타임에 읽거나 컨테이너 secret으로 주입합니다.

필수 파라미터:

```text
/ytshorts/OPENAI_API_KEY
/ytshorts/HF_TOKEN
/ytshorts/PIXABAY_API_KEY
/ytshorts/SLACK_WEBHOOK_URL
/ytshorts/S3_BUCKET_NAME
/ytshorts/YOUTUBE_CLIENT_ID
/ytshorts/YOUTUBE_CLIENT_SECRET
/ytshorts/YOUTUBE_REFRESH_TOKEN
/ytshorts/YOUTUBE_TOKEN_URI
```

선택 파라미터:

```text
/ytshorts/REDDIT_CLIENT_ID
/ytshorts/REDDIT_CLIENT_SECRET
/ytshorts/REDDIT_USER_AGENT
```

YouTube OAuth client/refresh token은 현재 SSM SecureString에 저장되어 있습니다. 값이 `PENDING`이면 publisher Lambda는 업로드를 시도하지 않고 `UPLOAD_BLOCKED` 상태를 남기도록 구현되어 있습니다.

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
