# 전체 구조

## 구조 선택 비교

| 항목 | 기존/중간 구조 | 최종 구조 | 선택 이유 |
| --- | --- | --- | --- |
| 스케줄 | CloudWatch Event Rule | EventBridge Scheduler | timezone, 입력 payload, 향후 one-time schedule 확장이 더 명확함 |
| 오케스트레이션 | Lambda가 EC2 단발 실행 | Step Functions | 단계별 재시도, 실패 지점 추적, 수동 재실행이 쉬움 |
| 렌더링 | EC2 userdata에서 전체 실행 | AWS Batch/Fargate | ffmpeg 같은 긴 작업을 Lambda에서 분리하고 컨테이너로 재현성 확보 |
| 업로드 | EC2 job 안에서 실행 | Lambda publisher | YouTube API 호출은 가벼운 I/O 작업이라 Lambda가 적합 |
| 상태 저장 | S3 JSON 중심 | S3 prefix + DynamoDB | 파일 산출물은 S3, 검색/상태/예약 관리는 DynamoDB가 적합 |
| 시크릿 | .env/access key 혼재 | SSM Parameter Store | Terraform state에 비밀값을 남기지 않음 |

최종 구조는 사용자가 제안한 방향과 동일한 계열이며, 운영/관측/재시도 측면에서 기존 EC2 런처보다 유리합니다.

## 런타임 구조

```text
EventBridge Scheduler
  ├─ twice-monthly refill input: {"mode":"generate","days":14}
  │   -> Step Functions ytshorts-pipeline
  │      -> Batch stage: collect
  │      -> Batch stage: filter
  │      -> Batch stage: script
  │      -> Batch stage: tts
  │      -> Batch stage: subtitles
  │      -> Batch array stage: render per content_id
  │      -> Batch stage: finalize
  │      -> S3 publish-ready + DynamoDB PUBLISH_READY
  └─ daily upload input: {"mode":"upload"}
      -> Step Functions ytshorts-pipeline
         -> Lambda ytshorts-publisher
         -> YouTube Data API
         -> DynamoDB UPLOADED
```

## S3 Prefix Contract

```text
raw/                  Reddit 원본 수집 결과
scripts/              필터링 결과, 대본, 실패 목록
audio/mp3/            Polly MP3
audio/marks/          Polly speech marks
audio/subtitles/      SRT 자막
videos/sources/       Pixabay 병합 배경 영상
videos/final/         최종 MP4
publish-ready/        업로드 대기 metadata
state/                중복 수집/legacy 호환 상태
```

## 코드 구조

```text
generator/text        Reddit 수집, 콘텐츠 필터링, 대본 생성
generator/tts         AWS Polly TTS와 음성 길이 분석
generator/video       배경 영상 병합, 자막 변환, 최종 렌더
shared/jobs           stage runner, generate/upload orchestration
shared/state          DynamoDB content repository
shared/storage        S3 object store 추상화
shared/utils          설정, S3, Slack 유틸
uploader              플랫폼 업로더와 YouTube OAuth helper
infra/terraform       AWS 인프라 코드
infra/bootstrap       Terraform remote state bootstrap
docs                  기획, 구조, 운영 문서
tests                 Reddit parser와 YouTube credential 테스트
```

## 생성 재고 정책

생성 스케줄은 매월 1일/15일 02:00 KST에 실행됩니다. `days=14`는 새 영상 14개를 무조건 만들라는 뜻이 아니라 daily upload를 14일 동안 유지할 목표 재고입니다.

실제 신규 생성 수는 다음과 같이 결정합니다.

```text
needed = min(max_new_items, max(0, target_days + buffer_days - current_publish_ready_count))
```

기본값은 `target_days=14`, `buffer_days=3`, `max_new_items=21`입니다. Reddit 후보 부족, GPT 검증 실패, TTS 길이 실패, 렌더 실패가 있으면 publish-ready가 목표보다 적을 수 있고, 다음 refill에서 부족분을 다시 채웁니다. 업로드 큐가 오래 밀려 예약일이 과거로 누적되면 publisher Lambda가 미업로드 큐를 현재 시점부터 다시 일별 슬롯으로 정렬합니다.

## Reddit 수집

Selenium DOM 크롤링은 제거했습니다. 현재 구조는 다음 순서입니다.

1. Reddit OAuth credential이 있으면 `oauth.reddit.com` listing API 사용
2. credential이 없으면 public JSON endpoint 시도
3. 403 또는 차단 시 PullPush API fallback 사용

이 방식은 Reddit DOM 변경에 덜 취약하며, 로컬 네트워크에서 Reddit public JSON이 차단되어도 수집을 계속할 수 있습니다.
