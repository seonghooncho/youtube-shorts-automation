# 완료 체크리스트

## 구조/설계

- [x] 기존 목적 분석: Reddit 사연을 쇼츠로 재가공해 YouTube 업로드
- [x] 기존 EC2 launcher 구조와 목표 구조 비교
- [x] 목표 구조 채택: EventBridge Scheduler + Step Functions + Batch/Fargate + Lambda publisher
- [x] S3 prefix contract 정의: `raw/`, `scripts/`, `audio/`, `videos/`, `publish-ready/`, `state/`
- [x] DynamoDB 콘텐츠 상태 모델 추가

## 코드 리팩터링

- [x] Selenium Reddit crawler 제거
- [x] Reddit OAuth/public JSON/PullPush fallback 수집기 구현
- [x] Batch stage runner 추가: `collect`, `filter`, `script`, `tts`, `subtitles`, `render`
- [x] 생성 일수와 Reddit 후보 수집량 분리
- [x] GPT 대본 생성은 요청된 생성 일수만큼만 수행하도록 제한
- [x] 빈 산출물을 성공으로 넘기지 않도록 stage artifact 검증 추가
- [x] S3Store upload/download/list/prefix sync 추상화
- [x] YouTube OAuth refresh token 기반 uploader 구현
- [x] upload scheduler를 새 `publish-ready/` prefix와 예약 시간 기준으로 수정
- [x] Lambda publisher 추가
- [x] Dockerfile/buildspec 추가

## AWS/Terraform

- [x] Terraform remote state S3 bucket 생성
- [x] Terraform lock DynamoDB table 생성
- [x] artifact S3 bucket Terraform 관리
- [x] ECR repository 생성
- [x] CodeBuild image build project 생성
- [x] Batch/Fargate compute environment, queue, job definition 생성
- [x] Step Functions state machine 생성
- [x] EventBridge Scheduler weekly/daily 생성
- [x] Publisher Lambda 생성
- [x] 기존 EventBridge Rule/Lambda launcher/EC2 role 제거
- [x] 제공된 credential을 SSM Parameter Store SecureString에 저장

## 검증

- [x] Python compileall 통과
- [x] pytest 통과
- [x] Terraform validate 통과
- [x] Terraform apply 완료
- [x] CodeBuild image build 완료
- [x] Step Functions upload smoke 완료
- [x] Batch collect smoke 완료
- [x] Reddit public JSON 403 상황에서 PullPush fallback 검증
- [x] YouTube OAuth refresh token 확보
- [x] 실제 YouTube upload E2E 확인
- [x] Step Functions generate smoke에서 상태 전달 버그 발견 및 `ResultPath = null` 수정

## 남은 외부 의존성

YouTube upload에는 API key가 아니라 OAuth client와 refresh token이 필요합니다. 현재 `/ytshorts/YOUTUBE_CLIENT_ID`, `/ytshorts/YOUTUBE_CLIENT_SECRET`, `/ytshorts/YOUTUBE_REFRESH_TOKEN`은 SSM SecureString에 저장되어 있습니다. 실제 upload E2E는 publish-ready 영상이 생성된 뒤 `private` 상태로 검증합니다.
