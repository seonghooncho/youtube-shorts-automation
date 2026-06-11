# 운영 문서

## 수동 실행

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
MODE=generate python runner.py
MODE=upload python runner.py
```

## AWS에서 수동 트리거

```bash
aws lambda invoke \
  --function-name ytshorts-launcher \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"generate"}' \
  /tmp/ytshorts-generate.json

aws lambda invoke \
  --function-name ytshorts-launcher \
  --cli-binary-format raw-in-base64-out \
  --payload '{"mode":"upload"}' \
  /tmp/ytshorts-upload.json
```

## 로그 위치

- EC2 userdata 로그: S3 `logs/userdata-*.log`
- 앱 실행 로그: S3 `logs/runner-*.log`
- 생성 상태: S3 `shorts/state/*.json`
- 최종 영상: S3 `shorts/videos/*.mp4`

## 업로드 안전장치

기본 업로드는 `private`이다. 실제 공개 업로드 전에는 SSM 또는 환경변수로 다음 값을 명시한다.

```bash
YOUTUBE_PRIVACY_STATUS=public
```
