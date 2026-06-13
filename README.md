# YouTube Shorts Automation

Reddit 사연을 쇼츠용 영상으로 각색하고, 음성/자막/배경 영상까지 합성한 뒤 YouTube에 자동 업로드하는 파이프라인입니다.

이 프로젝트의 핵심은 **영상 재고를 주기적으로 채워두고, 매일 정해진 시간에 1개씩 공개 업로드하는 자동화**입니다.

## 한눈에 보는 흐름

```text
Reddit/PullPush 사연 수집
  -> 업로드 가치가 있는 사연 필터링
  -> OpenAI로 쇼츠용 대본 각색
  -> AWS Polly로 TTS 생성
  -> speech marks 기반 자막 생성
  -> Pixabay에서 배경 영상 수집
  -> FFmpeg로 9:16 쇼츠 렌더링
  -> S3에 publish-ready 재고 저장
  -> 매일 YouTube에 public 업로드
```

## 전체 운영 구조

자동화는 AWS managed service를 중심으로 역할을 나눠 운영됩니다.

- **EventBridge Scheduler**: 월 2회 생성, 매일 업로드 트리거
- **Step Functions**: 수집부터 렌더링까지 단계별 오케스트레이션
- **AWS Batch/Fargate**: 오래 걸리는 ffmpeg 렌더링과 생성 작업 실행
- **Lambda**: 재고 계산과 YouTube 업로드처럼 가벼운 작업 담당
- **S3**: 원본, 대본, 음성, 자막, 최종 영상, publish-ready metadata 저장
- **DynamoDB**: 콘텐츠 상태, 예약 시각, 업로드 결과 저장
- **SSM Parameter Store**: 런타임 설정과 credential 관리

## 자동화 주기

```text
생성 refill: 매월 1일, 15일 02:00 KST
업로드:      매일 08:00 KST
```

생성은 “무조건 14개 생성”이 아니라 **재고 보충 방식**입니다.

```text
목표 재고 = 14일치 + 버퍼 3일 = 17개
신규 생성 수 = 목표 재고 - 현재 publish-ready 미업로드 재고
최대 신규 생성 cap = 21개
```

즉, 이미 업로드 대기 영상이 충분하면 생성 작업을 스킵하고, 부족하면 부족한 만큼만 생성합니다. 생성 중 일부 단계가 실패해 재고가 모자라면 다음 refill에서 다시 채웁니다.

## 결과물이 저장되는 곳

생성된 파일은 S3 bucket `youtube-shorts-automation-160885253413-apne2`에 저장됩니다.

```text
raw/                  수집된 Reddit/PullPush 원본
scripts/              필터링 결과, 대본, 메타데이터
audio/mp3/            Polly TTS 음성
audio/marks/          Polly speech marks
audio/subtitles/      SRT 자막
videos/sources/       배경 영상 소스
videos/final/         최종 MP4
publish-ready/        업로드 대기 metadata
state/                중복 방지와 상태 파일
```

콘텐츠별 상태와 YouTube 업로드 결과는 DynamoDB `ytshorts-content`에 기록됩니다.

## YouTube 업로드

업로드 대상은 YouTube 채널이며, 현재 자동 업로드 기본값은 `public`입니다.

업로드는 API key가 아니라 YouTube OAuth refresh token으로 수행합니다. 업로드가 끝나면 metadata와 DynamoDB에 YouTube video ID가 기록됩니다.

## 영상 품질 처리

쇼츠 화면에서 자막과 배경이 흐려지는 문제를 줄이기 위해 렌더링 구조를 조정했습니다.

- 최종 영상은 1080x1920 세로형으로 정규화
- 자막은 중앙 배치, Anton 폰트, 굵은 외곽선 스타일
- 자막은 4:4:4 중간 프레임에 burn-in 후 최종 H.264 MP4로 인코딩
- Pixabay 영상은 FHD 이상 소스만 기본 사용
- 다운로드한 배경 영상은 선명도 점수로 한 번 더 필터링
- 최종 MP4는 업로드 전 파일 크기, 길이, 해상도, 오디오/비디오 스트림을 검증

## 코드 구조

```text
generator/text        사연 수집, 필터링, 대본 생성
generator/tts         Polly TTS와 음성 길이 처리
generator/video       배경 영상 선택, 자막, 최종 렌더링
shared/jobs           생성/업로드 stage runner
shared/storage        S3 저장소 추상화
shared/state          DynamoDB 상태 저장
uploader              YouTube 업로드/OAuth helper
infra/terraform       AWS 인프라 정의
infra/terraform/lambda
                      planner, publisher, budget notifier Lambda
docs                  기획, 구조, 운영, 품질 문서
tests                 핵심 로직 회귀 테스트
```

## 주요 AWS 리소스

```text
Step Functions: ytshorts-pipeline
Batch queue:    ytshorts-pipeline
ECR repo:       ytshorts-app
CodeBuild:      ytshorts-image-build
Planner Lambda: ytshorts-planner
Publisher:      ytshorts-publisher
S3 bucket:      youtube-shorts-automation-160885253413-apne2
DynamoDB:       ytshorts-content
```

Terraform remote state는 S3와 DynamoDB lock table로 관리합니다.

## 설정 관리

런타임 설정은 `/ytshorts/*` SSM Parameter Store를 기준으로 관리합니다.

예를 들어 공개 범위, 생성 재고 수, 자막 크기, 렌더 품질, Pixabay 필터 기준 같은 값은 SSM에서 바꿀 수 있습니다. Batch 컨테이너는 SSM 값을 주입받고, Lambda는 런타임에 SSM 값을 읽습니다.

credential 값은 README에 적지 않습니다. 필요한 파라미터와 운영 방법은 [운영 문서](docs/OPERATIONS.md)와 [인프라 문서](docs/INFRASTRUCTURE.md)에 정리되어 있습니다.

## 더 자세한 문서

- [기획서](docs/PRODUCT_SPEC.md)
- [전체 구조](docs/ARCHITECTURE.md)
- [AWS 인프라](docs/INFRASTRUCTURE.md)
- [운영 문서](docs/OPERATIONS.md)
- [품질 자동화](docs/QUALITY_AUTOMATION.md)
- [완료 체크리스트](docs/IMPLEMENTATION_CHECKLIST.md)
