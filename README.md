# YouTube Shorts Automation

Reddit 사연을 수집해 대본, TTS, 자막, 배경 영상, 최종 렌더링, YouTube 업로드까지 자동화하는 배치 프로젝트입니다.

## Pipeline

```text
Reddit/PullPush -> OpenAI filter/script -> AWS Polly -> SRT -> Pixabay -> MoviePy/FFmpeg -> S3 -> YouTube
```

## Local Run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

MODE=generate python runner.py
MODE=upload python runner.py
```

## AWS Automation

Terraform 구성은 `infra/terraform`에 있습니다. EventBridge가 Lambda launcher를 호출하고, Lambda가 단발 EC2 작업을 띄운 뒤 EC2가 종료되면 자동으로 terminate됩니다.

```bash
cd infra/bootstrap
terraform init
terraform apply

cd infra/terraform
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
