# YouTube Shorts Automation

Reddit 사연을 수집해 대본, TTS, 자막, 배경 영상, 최종 렌더링, YouTube 업로드까지 자동화하는 배치 프로젝트입니다.

## Pipeline

```text
EventBridge Scheduler
  -> Step Functions
    -> AWS Batch/Fargate stage jobs
      -> Reddit/PullPush -> OpenAI -> Polly -> SRT -> Pixabay -> FFmpeg -> S3
  -> Lambda publisher
      -> YouTube Data API upload
```

## AWS Runtime

- 매월 1일/15일 `ytshorts-generate-refill` Scheduler가 publish-ready 재고를 14일 목표 + 버퍼 기준으로 보충합니다.
- 매일 `ytshorts-upload-daily` Scheduler가 publish-ready 영상을 YouTube에 업로드합니다.
- 생성 단계는 AWS Batch/Fargate 컨테이너에서 실행됩니다.
- 렌더링은 Batch array job으로 영상 단위 병렬 처리 후 finalize stage에서 S3/DynamoDB 상태를 확정합니다.
- 업로드는 `ytshorts-publisher` Lambda가 S3 metadata와 영상을 읽어 처리합니다.
- 상태는 S3 prefix와 DynamoDB `ytshorts-content`에 함께 기록됩니다.

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
MODE=upload python runner.py
```

## Terraform

Terraform remote state는 S3와 DynamoDB lock table로 관리합니다.

```bash
cd infra/bootstrap
terraform init
terraform apply

cd ../terraform
terraform init
terraform plan
terraform apply
```

시크릿은 Terraform state에 넣지 않고 AWS SSM Parameter Store `/ytshorts/*` SecureString으로 관리합니다.

## Docs

- [기획서](docs/PRODUCT_SPEC.md)
- [전체 구조](docs/ARCHITECTURE.md)
- [AWS Terraform 설계](docs/INFRASTRUCTURE.md)
- [운영 문서](docs/OPERATIONS.md)
- [품질 자동화](docs/QUALITY_AUTOMATION.md)
- [완료 체크리스트](docs/IMPLEMENTATION_CHECKLIST.md)
