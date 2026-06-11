# AWS Terraform 설계

## 생성 리소스

- S3 bucket: 영상, 상태 파일, 로그 저장
- SSM Parameter Store: 런타임 설정과 시크릿 저장
- IAM role/instance profile: EC2 job 권한
- IAM role: Lambda launcher 권한
- Lambda: generate/upload 모드의 단발 EC2 실행
- EventBridge rules: 생성/업로드 주기 실행
- Security group: job EC2 outbound-only

## 시크릿 관리

시크릿 값은 Terraform 변수로 받지 않는다. Terraform state에 민감 값이 남지 않도록 `/ytshorts/*` SSM SecureString을 런타임에 읽는다.

필수 파라미터:

```text
/ytshorts/OPENAI_API_KEY
/ytshorts/PIXABAY_API_KEY
/ytshorts/SLACK_WEBHOOK_URL
/ytshorts/S3_BUCKET_NAME
/ytshorts/YOUTUBE_CLIENT_ID
/ytshorts/YOUTUBE_CLIENT_SECRET
/ytshorts/YOUTUBE_REFRESH_TOKEN
```

선택 파라미터:

```text
/ytshorts/REDDIT_CLIENT_ID
/ytshorts/REDDIT_CLIENT_SECRET
/ytshorts/REDDIT_USER_AGENT
```

Reddit OAuth 값이 없으면 PullPush fallback을 사용한다.

## 명령

### Remote state bootstrap

Terraform state는 별도 S3 bucket과 DynamoDB lock table에서 관리한다.

```bash
cd infra/bootstrap
terraform init
terraform apply
```

생성 리소스:

```text
S3 bucket: ytshorts-terraform-state-160885253413-apne2
DynamoDB table: ytshorts-terraform-locks
```

### Application infrastructure

```bash
cd infra/terraform
terraform init
terraform import aws_s3_bucket.artifacts youtube-shorts-automation-160885253413-apne2
terraform import aws_s3_bucket_public_access_block.artifacts youtube-shorts-automation-160885253413-apne2
terraform import aws_s3_bucket_server_side_encryption_configuration.artifacts youtube-shorts-automation-160885253413-apne2
terraform import aws_s3_bucket_versioning.artifacts youtube-shorts-automation-160885253413-apne2
terraform plan
terraform apply
```

새 계정에서 처음부터 만들 때는 import 없이 `terraform apply`만 실행한다.

## 현재 적용 결과

```text
Artifact bucket: youtube-shorts-automation-160885253413-apne2
Launcher Lambda: ytshorts-launcher
Generate schedule: ytshorts-generate
Upload schedule: ytshorts-upload
EC2 instance profile: ytshorts-ec2-job-profile
```
