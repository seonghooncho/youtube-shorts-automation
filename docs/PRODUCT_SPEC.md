# 기획서

## 목적

Reddit의 긴 사연형 게시물을 짧은 세로 영상으로 재가공해 YouTube Shorts에 주기적으로 업로드한다. 사용자가 매번 소재 선정, 대본 작성, TTS 생성, 배경 영상 편집, 업로드를 수동으로 하지 않도록 배치 자동화를 구축한다.

## 콘텐츠 흐름

1. Reddit 사연 수집
2. 쇼츠에 적합한 사연 필터링
3. OpenAI로 1인칭 영어 내레이션 대본과 메타데이터 생성
4. AWS Polly로 음성 및 speech marks 생성
5. speech marks를 SRT 자막으로 변환
6. Pixabay 배경 영상을 세로형으로 병합
7. MoviePy/FFmpeg로 최종 쇼츠 렌더링
8. S3에 영상과 상태 파일 저장
9. 업로드 스케줄러가 미업로드 영상을 YouTube에 업로드

## 운영 원칙

- 업로드 기본값은 `private`이다. 공개 업로드는 `YOUTUBE_PRIVACY_STATUS=public`로 명시한다.
- YouTube 업로드는 API key가 아니라 OAuth refresh token을 사용한다.
- Reddit 공식 API 또는 public JSON이 막히면 PullPush fallback을 사용한다.
- 상태 파일은 S3 `shorts/state/*`, 영상은 `shorts/videos/*`에 저장한다.
- 생성과 업로드는 분리된 스케줄로 실행한다.
