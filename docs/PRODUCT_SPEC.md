# 기획서

## 목적

Reddit의 긴 사연형 게시물을 짧은 세로 영상으로 재가공해 YouTube Shorts에 주기적으로 업로드합니다. 사용자가 소재 선정, 대본 작성, TTS 생성, 배경 영상 편집, 업로드를 매번 수동으로 하지 않도록 AWS 기반 자동 생성/업로드 파이프라인을 구축합니다.

## 사용자 가치

- 매월 1일/15일에 14일 목표 재고와 버퍼를 기준으로 publish-ready 영상을 보충합니다.
- 매일 정해진 시간에 publish-ready 영상 1건을 업로드합니다.
- 업로드 기본값은 `private`라서 자동화 중에도 공개 사고를 줄입니다.
- 콘텐츠 상태와 업로드 결과를 DynamoDB에서 추적합니다.
- publish-ready 재고가 충분하면 생성 Batch를 시작하지 않아 불필요한 비용을 줄입니다.

## 콘텐츠 흐름

1. Reddit 또는 PullPush에서 사연형 게시물 수집
2. OpenAI로 쇼츠 적합성 필터링
3. OpenAI로 1인칭 영어 내레이션 대본과 메타데이터 생성
4. planner Lambda가 기존 publish-ready 재고를 확인하고 부족분을 계산
5. 부족분이 있으면 신규 항목에 예약일 부여
6. AWS Polly로 음성 및 speech marks 생성
7. speech marks를 SRT 자막으로 변환
8. Pixabay 배경 영상을 세로형으로 병합
9. MoviePy/FFmpeg로 최종 쇼츠 렌더링 및 MP4 유효성 검증
10. S3 `publish-ready/`와 DynamoDB에 업로드 대기 상태 저장
11. daily upload workflow가 예약 시간이 지난 영상을 YouTube에 업로드

## 운영 원칙

- 생성과 업로드는 분리된 workflow로 실행합니다.
- Lambda는 YouTube API 호출 같은 가벼운 I/O 작업만 담당합니다.
- 긴 ffmpeg 렌더링은 AWS Batch/Fargate로 실행합니다.
- 시크릿은 SSM Parameter Store SecureString으로 관리합니다.
- Reddit 수집은 DOM 크롤링 대신 API-first/fallback 구조로 운영합니다.
- 14일 목표 재고는 보장 목표이며, 실제 생성 실패분은 버퍼와 다음 refill에서 보정합니다.
- 너무 작거나 스트림이 깨진 MP4는 publish-ready 또는 YouTube 업로드로 승격하지 않습니다.
