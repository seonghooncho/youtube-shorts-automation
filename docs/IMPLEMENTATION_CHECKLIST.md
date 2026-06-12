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
- [x] GPT 대본 생성은 publish-ready 재고 부족분만큼만 수행하도록 제한
- [x] OpenAI 필터 모델과 대본 모델 분리
- [x] `gpt-5.4-*`/`gpt-5.5` Responses API reasoning 파라미터 호환성 반영
- [x] 대본 생성 기본 모델을 품질 우선 `gpt-5.5`로 상향
- [x] 대본 Structured Outputs 스키마에 `source_summary`, `story_beats` 추가
- [x] 대본 로컬 품질검증 추가: 훅, 길이, 결말 질문, 원문 overlap, 메타 표현, visual keyword
- [x] Reddit 원문 integrity metadata 추가: 길이, 단어 수, hash, truncation flag
- [x] Reddit accepted post detail 재조회로 listing body 누락/축약 방지
- [x] PullPush fallback에서 `selftext` 외 `body`/`text` 원문 fallback 추가
- [x] 원문이 너무 얇거나 잘림 의심이면 필터/대본 생성 전 스킵
- [x] publish-ready 버퍼/백필 정책 추가
- [x] 오래 밀린 업로드 큐 예약일 rebase 보정 추가
- [x] 렌더링 stage를 Batch array job으로 병렬화
- [x] planner Lambda로 publish-ready 재고 충분 시 Batch 전체 스킵
- [x] 렌더링 array size를 부족 수량(`needed_new_items`) 기준으로 동적 조정
- [x] 부족 수량 1개일 때 단건 render job 경로 추가
- [x] 빈 산출물을 성공으로 넘기지 않도록 stage artifact 검증 추가
- [x] 최종 MP4 ffprobe 검증 추가: 크기, 길이, 해상도, 오디오/비디오 스트림
- [x] publisher Lambda 업로드 전 최소 파일 크기 차단 추가
- [x] Pixabay used-id array shard 경합 완화 및 finalize 병합 추가
- [x] Pixabay page offset을 content/query 기준으로 분산해 동시 shard 중복 선택 완화
- [x] S3Store upload/download/list/prefix sync 추상화
- [x] YouTube OAuth refresh token 기반 uploader 구현
- [x] YouTube OAuth scope를 상태조회/삭제 가능한 `youtube.force-ssl` 포함으로 확장
- [x] upload scheduler를 새 `publish-ready/` prefix와 예약 시간 기준으로 수정
- [x] Lambda publisher 추가
- [x] Lambda planner 추가
- [x] Dockerfile/buildspec 추가

## AWS/Terraform

- [x] Terraform remote state S3 bucket 생성
- [x] Terraform lock DynamoDB table 생성
- [x] artifact S3 bucket Terraform 관리
- [x] ECR repository 생성
- [x] CodeBuild image build project 생성
- [x] Batch/Fargate compute environment, queue, stage/script/render job definition 생성
- [x] Step Functions state machine 생성
- [x] EventBridge Scheduler twice-monthly refill/daily upload 생성
- [x] Planner Lambda 생성
- [x] Publisher Lambda 생성
- [x] Budget/Batch/Step Functions 실패 Slack 알림 경로 생성
- [x] S3 lifecycle, ECR lifecycle, CloudWatch log retention 설정
- [x] 기존 EventBridge Rule/Lambda launcher/EC2 role 제거
- [x] 제공된 credential을 SSM Parameter Store SecureString에 저장

## 검증

- [x] Python compileall 통과
- [x] pytest 통과
- [x] Terraform validate 통과
- [x] Terraform apply 완료
- [x] CodeBuild image build 완료
- [x] CodeBuild image build #16 완료: ECR `ytshorts-app:latest` 갱신
- [x] Step Functions upload smoke 완료
- [x] planner Lambda smoke 완료: `days=0`, `buffer_days=0`, `max_new_items=0` → `should_generate=false`
- [x] Step Functions planner skip smoke 완료: `GenerateSkipped`로 Batch 미실행 성공
- [x] publisher Lambda smoke 완료: 예약 도래 항목 없음 → no-op
- [x] Batch collect smoke 완료
- [x] Reddit public JSON 403 상황에서 PullPush fallback 검증
- [x] YouTube OAuth refresh token 확보
- [x] 실제 YouTube upload E2E 확인
- [x] Step Functions generate smoke에서 상태 전달 버그 발견 및 `ResultPath = null` 수정
- [x] YouTube 처리중 원인 분석: 2026-06-12 기준 2초/9KB smoke MP4 업로드로 확인
- [x] 현재 OAuth scope 한계 확인: `videos.list`/`videos.delete`는 `ACCESS_TOKEN_SCOPE_INSUFFICIENT`

## 남은 외부 의존성

YouTube upload에는 API key가 아니라 OAuth client와 refresh token이 필요합니다. 현재 `/ytshorts/YOUTUBE_CLIENT_ID`, `/ytshorts/YOUTUBE_CLIENT_SECRET`, `/ytshorts/YOUTUBE_REFRESH_TOKEN`은 SSM SecureString에 저장되어 있습니다.

2026-06-12에 업로드된 smoke 영상 `Ra2dUfPJmJE`는 현재 token scope로 API 삭제/상태조회가 불가능합니다. 새 refresh token을 발급할 때는 코드에 반영된 `youtube.upload` + `youtube.force-ssl` scope로 재인증해야 처리 상태 조회와 삭제까지 가능합니다.
