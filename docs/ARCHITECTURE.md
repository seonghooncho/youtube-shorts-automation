# 전체 구조

## 런타임 구조

```text
EventBridge schedule
  -> Lambda launcher
    -> one-shot EC2 instance
      -> git clone repo
      -> load /ytshorts/* from SSM Parameter Store
      -> python runner.py
      -> upload logs/state/videos to S3
      -> shutdown with terminate behavior
```

## 코드 구조

```text
generator/text      Reddit 수집, 콘텐츠 필터링, 대본 생성
generator/tts       AWS Polly TTS와 음성 길이 분석
generator/video     배경 영상 병합, 자막 변환, 최종 렌더
shared/jobs         generate/upload 실행 오케스트레이션
shared/storage      S3 object store 추상화
shared/utils        설정, S3, Slack 유틸
uploader            플랫폼 업로더
infra/terraform     AWS 인프라 코드
infra/bootstrap     Terraform remote state 부트스트랩
docs                기획, 구조, 운영 문서
tests               핵심 파서와 인증 로직 테스트
```

## 주요 리팩터링 포인트

- 경로 기준을 `shared/utils`가 아니라 프로젝트 루트로 수정했다.
- Selenium 기반 Reddit DOM 크롤링을 API-first 수집기로 교체했다.
- S3와 Polly는 하드코딩 access key 대신 AWS credential chain/IAM role을 우선 사용한다.
- Slack webhook 누락 시 파이프라인 전체가 죽지 않도록 했다.
- YouTube 업로드는 OAuth refresh token 기반으로 바꿨다.
- Instagram/TikTok은 명시적으로 활성화한 경우에만 실행된다.
