#!/bin/bash
# 모든 로그를 /var/log/userdata.log 로 남김 + 콘솔에도 출력
exec > >(tee /var/log/userdata.log | logger -t user-data -s 2>/dev/console) 2>&1
set -euo pipefail

# ===== 무조건 종료 보장 (성공/실패 상관없이) =====
cleanup() {
  ( sleep 900; shutdown -h now ) >/dev/null 2>&1 &   # 15분 세이프가드
  shutdown -h now || poweroff || halt || true        # 즉시 종료 시도
}
trap cleanup EXIT

export DEBIAN_FRONTEND=noninteractive
retry() { n=0; until "$@"; do n=$((n+1)); [[ $n -ge 5 ]] && return 1; echo "retry $n..."; sleep 5; done; }

# ===== 기본 패키지 =====
retry apt-get update -y
retry apt-get install -y --no-install-recommends git awscli openssh-client jq ca-certificates

# ===== 작업 디렉터리 =====
APP_ROOT=/opt/ytshorts
REPO_DIR="$APP_ROOT/repo"
install -d -m 755 "$APP_ROOT"
chown -R ubuntu:ubuntu "$APP_ROOT"

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ===== Deploy Key 복구(파이프 제거, 실패 안전) =====
install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/.ssh
DEPLOY_KEY="$(aws ssm get-parameter --name "/ytshorts/DEPLOY_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || true)"
if [[ -n "${DEPLOY_KEY}" && "${DEPLOY_KEY}" != "None" ]]; then
  printf '%s\n' "$DEPLOY_KEY" > /home/ubuntu/.ssh/id_ed25519
  chown ubuntu:ubuntu /home/ubuntu/.ssh/id_ed25519
  chmod 600 /home/ubuntu/.ssh/id_ed25519
  # 호스트키/SSH 설정
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
  echo "WARN: SSM /ytshorts/DEPLOY_KEY not found or no permission; cloning via HTTPS."
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
if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
else
  retry apt-get install -y python3 python3-venv
  PY=python3
fi

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
    echo "ℹ️ ffmpeg not found; imageio-ffmpeg will download one on first use."
    unset IMAGEIO_FFMPEG_EXE || true
  fi
fi

# ===== 앱 시크릿: SSM에서 로드 =====
export OPENAI_API_KEY="$(aws ssm get-parameter --name "/ytshorts/OPENAI_API_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export YOUTUBE_API_KEY="$(aws ssm get-parameter --name "/ytshorts/YOUTUBE_API_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export SLACK_WEBHOOK_URL="$(aws ssm get-parameter --name "/ytshorts/SLACK_WEBHOOK_URL" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export PIXABAY_API_KEY="$(aws ssm get-parameter --name "/ytshorts/PIXABAY_API_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export MODE="generate"

# S3/Polly 액세스 키
export AWS_S3_ACCESS_KEY="$(aws ssm get-parameter --name "/ytshorts/AWS_S3_ACCESS_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export AWS_S3_SECRET_ACCESS_KEY="$(aws ssm get-parameter --name "/ytshorts/AWS_S3_SECRET_ACCESS_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export AWS_POLLY_ACCESS_KEY_ID="$(aws ssm get-parameter --name "/ytshorts/AWS_POLLY_ACCESS_KEY_ID" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"
export AWS_POLLY_SECRET_ACCESS_KEY="$(aws ssm get-parameter --name "/ytshorts/AWS_POLLY_SECRET_ACCESS_KEY" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"

# 버킷 이름
export S3_BUCKET_NAME="$(aws ssm get-parameter --name "/ytshorts/S3_BUCKET_NAME" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"

# ===== 파이썬 의존성 =====
if [[ -f "$REPO_DIR/requirements-batch-generate.txt" ]]; then
  pip install --no-index --find-links=/opt/wheelhouse -r "$REPO_DIR/requirements-batch-generate.txt" || \
  pip install -r "$REPO_DIR/requirements-batch-generate.txt"
else
  pip install moviepy imageio-ffmpeg openai selenium webdriver-manager tqdm "Pillow<10" pysrt python-dotenv boto3 requests
fi

# ===== 실행 =====
( set -x; python runner.py )
