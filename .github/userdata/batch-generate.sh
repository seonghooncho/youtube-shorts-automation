#!/bin/bash
# 모든 로그 -> 파일/콘솔
exec > >(tee /var/log/userdata.log | logger -t user-data -s 2>/dev/console) 2>&1
set -euo pipefail
set -o errtrace
export PS4='+ $(date "+%H:%M:%S") [${BASH_SOURCE##*/}:${LINENO}] '
set -x

# ===== 종료 보장 (성공/실패 상관없이) =====
cleanup() {
  # 실행 중 만든 로그들 S3로 업로드(가능할 때만)
  if [[ -n "${S3_BUCKET_NAME:-}" ]]; then
    aws s3 cp /var/log/userdata.log "s3://${S3_BUCKET_NAME}/logs/userdata-$(date +%Y%m%d-%H%M%S).log" || true
    [[ -d /opt/ytshorts/logs ]] && aws s3 cp /opt/ytshorts/logs/ "s3://${S3_BUCKET_NAME}/logs/" --recursive || true
  fi
  ( sleep 900; shutdown -h now ) >/dev/null 2>&1 &   # 15분 세이프가드
  shutdown -h now || poweroff || halt || true        # 즉시 종료 시도
}
trap cleanup EXIT
trap 'RC=$?; echo "❌ ERR at line ${LINENO}: ${BASH_COMMAND} (rc=$RC)"; exit $RC' ERR

export DEBIAN_FRONTEND=noninteractive
REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"
export AWS_DEFAULT_REGION="$REGION"

retry() { n=0; until "$@"; do n=$((n+1)); [[ $n -ge 5 ]] && return 1; echo "retry $n..."; sleep 5; done; }

# ---- SSM 파라미터 헬퍼 (명시적 리전) ----
get_param() {
  local name="$1"
  aws ssm get-parameter --name "$name" --with-decryption --region "$REGION" --query 'Parameter.Value' --output text
}

# ===== 기본 패키지 =====
retry apt-get update -y
retry apt-get install -y --no-install-recommends git awscli openssh-client jq ca-certificates

# ===== 작업 디렉터리 =====
APP_ROOT=/opt/ytshorts
REPO_DIR="$APP_ROOT/repo"
install -d -m 755 "$APP_ROOT"
chown -R ubuntu:ubuntu "$APP_ROOT"

# ===== Deploy Key 복구 (가능하면 SSH, 아니면 HTTPS) =====
install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
set +e
DEPLOY_KEY="$(get_param "/ytshorts/DEPLOY_KEY" 2>/dev/null)"
set -e
if [[ -n "${DEPLOY_KEY:-}" && "${DEPLOY_KEY}" != "None" ]]; then
  printf '%s\n' "$DEPLOY_KEY" > /home/ubuntu/.ssh/id_ed25519
  chown ubuntu:ubuntu /home/ubuntu/.ssh/id_ed25519
  chmod 600 /home/ubuntu/.ssh/id_ed25519
  sudo -u ubuntu ssh-keyscan -t rsa github.com >> /home/ubuntu/.ssh/known_hosts
  cat > /home/ubuntu/.ssh/config <<'CONF'
Host github.com
  HostName github.com
  User git
  IdentityFile /home/ubuntu/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
CONF
  chown ubuntu:ubuntu /home/ubuntu/.ssh/known_hosts /home/ubuntu/.ssh/config
  chmod 644 /home/ubuntu/.ssh/known_hosts
  chmod 600 /home/ubuntu/.ssh/config
  GIT_URL="git@github.com:seonghooncho/youtube-shorts-automation.git"
else
  echo "WARN: /ytshorts/DEPLOY_KEY 없음/권한없음 → HTTPS로 클론"
  GIT_URL="https://github.com/seonghooncho/youtube-shorts-automation.git"
fi

# ===== 레포 클론/업데이트 (존재해도 안전) =====
if [[ -d "$REPO_DIR/.git" ]]; then
  sudo -u ubuntu git -C "$REPO_DIR" fetch --depth=1 origin || sudo -u ubuntu git -C "$REPO_DIR" remote add origin "$GIT_URL"
  sudo -u ubuntu git -C "$REPO_DIR" reset --hard origin/HEAD || sudo -u ubuntu git -C "$REPO_DIR" reset --hard origin/main || true
else
  sudo -u ubuntu rm -rf "$REPO_DIR"
  sudo -u ubuntu git clone --depth=1 "$GIT_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
chown -R ubuntu:ubuntu "$REPO_DIR"

# ===== Python 3.x + venv =====
if ! command -v python3.11 >/dev/null 2>&1; then
  retry apt-get install -y python3.11 python3.11-venv || true
fi
if command -v python3.11 >/dev/null 2>&1; then PY=python3.11; else retry apt-get install -y python3 python3-venv; PY=python3; fi

rm -rf .venv
$PY -m venv .venv
. .venv/bin/activate
pip install -U pip

# ===== ffmpeg =====
if command -v ffmpeg >/dev/null 2>&1; then
  export IMAGEIO_FFMPEG_EXE="$(command -v ffmpeg)"
else
  retry apt-get install -y ffmpeg || true
  if command -v ffmpeg >/dev/null 2>&1; then
    export IMAGEIO_FFMPEG_EXE="$(command -v ffmpeg)"
  else
    echo "ℹ️ ffmpeg 없음: imageio-ffmpeg가 최초 사용 시 바이너리 다운로드"
    unset IMAGEIO_FFMPEG_EXE || true
  fi
fi

# ===== 앱 시크릿: SSM에서 로드 (에러 숨기지 않음) =====
OPENAI_API_KEY="$(get_param "/ytshorts/OPENAI_API_KEY" || echo "")"
YOUTUBE_API_KEY="$(get_param "/ytshorts/YOUTUBE_API_KEY" || echo "")"
SLACK_WEBHOOK_URL="$(get_param "/ytshorts/SLACK_WEBHOOK_URL" || echo "")"
PIXABAY_API_KEY="$(get_param "/ytshorts/PIXABAY_API_KEY" || echo "")"
MODE="generate"

AWS_S3_ACCESS_KEY="$(get_param "/ytshorts/AWS_S3_ACCESS_KEY" || echo "")"
AWS_S3_SECRET_ACCESS_KEY="$(get_param "/ytshorts/AWS_S3_SECRET_ACCESS_KEY" || echo "")"
AWS_POLLY_ACCESS_KEY_ID="$(get_param "/ytshorts/AWS_POLLY_ACCESS_KEY_ID" || echo "")"
AWS_POLLY_SECRET_ACCESS_KEY="$(get_param "/ytshorts/AWS_POLLY_SECRET_ACCESS_KEY" || echo "")"

S3_BUCKET_NAME="$(get_param "/ytshorts/S3_BUCKET_NAME" || echo "")"

export OPENAI_API_KEY YOUTUBE_API_KEY SLACK_WEBHOOK_URL PIXABAY_API_KEY MODE
export AWS_S3_ACCESS_KEY AWS_S3_SECRET_ACCESS_KEY AWS_POLLY_ACCESS_KEY_ID AWS_POLLY_SECRET_ACCESS_KEY
export S3_BUCKET_NAME

echo "SSM sanity snapshot:"
for v in OPENAI_API_KEY YOUTUBE_API_KEY SLACK_WEBHOOK_URL PIXABAY_API_KEY S3_BUCKET_NAME; do
  eval "val=\${$v:-}"
  if [[ -z "$val" ]]; then echo "❌ $v is EMPTY"; else echo "✅ $v loaded (len=${#val})"; fi
done

# ---- (옵션) 커스텀 S3 키 → 표준 AWS 변수 브릿지 ----
if [[ -n "${AWS_S3_ACCESS_KEY:-}" && -n "${AWS_S3_SECRET_ACCESS_KEY:-}" ]]; then
  export AWS_ACCESS_KEY_ID="$AWS_S3_ACCESS_KEY"
  export AWS_SECRET_ACCESS_KEY="$AWS_S3_SECRET_ACCESS_KEY"
fi

# ---- 필수값 강제 체크 ----
require() { name="$1"; val="${!name:-}"; if [[ -z "$val" ]]; then echo "FATAL: $name is empty (check SSM name/region/perms)"; exit 90; fi; }
require S3_BUCKET_NAME
require SLACK_WEBHOOK_URL
# 필요 시 enable: require OPENAI_API_KEY

# ---- S3 헬스 체크 (특정 버킷만) ----
aws s3api head-bucket --bucket "$S3_BUCKET_NAME" --region "$REGION" || echo "❌ head-bucket failed (권한/리전/버킷 존재 여부 확인)"

# ===== 파이썬 의존성 =====
if [[ -f "$REPO_DIR/requirements-batch-generate.txt" ]]; then
  pip install --no-index --find-links=/opt/wheelhouse -r "$REPO_DIR/requirements-batch-generate.txt" || \
  pip install -r "$REPO_DIR/requirements-batch-generate.txt"
else
  pip install moviepy imageio-ffmpeg openai selenium webdriver-manager tqdm "Pillow<10" pysrt python-dotenv boto3 requests
fi

# ===== 실행 (로그 파일 저장) =====
LOG_DIR=/opt/ytshorts/logs
mkdir -p "$LOG_DIR"
( set -x; python runner.py ) 2>&1 | tee "$LOG_DIR/runner-$(date +%Y%m%d-%H%M%S).log"
