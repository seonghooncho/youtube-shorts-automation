#!/bin/bash
# 모든 로그를 /var/log/userdata.log 로 남김 + 콘솔에도 출력
exec > >(tee /var/log/userdata.log | logger -t user-data -s 2>/dev/console) 2>&1
set -euxo pipefail

# 기본 패키지
apt-get update -y
apt-get install -y --no-install-recommends git awscli openssh-client jq ca-certificates

# 작업 디렉토리
mkdir -p /opt/ytshorts
chown -R ubuntu:ubuntu /opt/ytshorts
cd /opt/ytshorts

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-ap-northeast-2}"

# ===== Deploy Key 복구 =====
sudo -u ubuntu mkdir -p /home/ubuntu/.ssh
aws ssm get-parameter --name "/ytshorts/DEPLOY_KEY" --with-decryption --output json \
  | jq -r .Parameter.Value > /home/ubuntu/.ssh/id_ed25519
chown ubuntu:ubuntu /home/ubuntu/.ssh/id_ed25519
chmod 600 /home/ubuntu/.ssh/id_ed25519

# GitHub 호스트키 등록 + SSH 설정
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

# ===== 레포 클론 =====
sudo -u ubuntu git clone --depth=1 git@github.com:seonghooncho/youtube-shorts-automation.git /opt/ytshorts || {
  echo "ERROR: git clone failed"; exit 1;
}
chown -R ubuntu:ubuntu /opt/ytshorts
cd /opt/ytshorts

# ===== 앱 시크릿: SSM에서 로드 =====
export OPENAI_API_KEY="$(aws ssm get-parameter --name "/ytshorts/OPENAI_API_KEY" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export YOUTUBE_API_KEY="$(aws ssm get-parameter --name "/ytshorts/YOUTUBE_API_KEY" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export SLACK_WEBHOOK_URL="$(aws ssm get-parameter --name "/ytshorts/SLACK_WEBHOOK_URL" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export PIXABAY_API_KEY="$(aws ssm get-parameter --name "/ytshorts/PIXABAY_API_KEY" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export MODE="generate"

# S3/Polly 액세스 키
export AWS_S3_ACCESS_KEY="$(aws ssm get-parameter --name "/ytshorts/AWS_S3_ACCESS_KEY" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export AWS_S3_SECRET_ACCESS_KEY="$(aws ssm get-parameter --name "/ytshorts/AWS_S3_SECRET_ACCESS_KEY" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export AWS_POLLY_ACCESS_KEY_ID="$(aws ssm get-parameter --name "/ytshorts/AWS_POLLY_ACCESS_KEY_ID" --with-decryption --query 'Parameter.Value' --output text || echo "")"
export AWS_POLLY_SECRET_ACCESS_KEY="$(aws ssm get-parameter --name "/ytshorts/AWS_POLLY_SECRET_ACCESS_KEY" --with-decryption --query 'Parameter.Value' --output text || echo "")"

# 버킷 이름
export S3_BUCKET_NAME="$(aws ssm get-parameter --name "/ytshorts/S3_BUCKET_NAME" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")"

# ===== Python 3.x + venv =====
if ! command -v python3.11 >/dev/null 2>&1; then
  apt-get install -y python3.11 python3.11-venv || true
fi
if command -v python3.11 >/dev/null 2>&1; then
  PY=python3.11
else
  apt-get install -y python3 python3-venv
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
  apt-get install -y ffmpeg || true
  if command -v ffmpeg >/dev/null 2>&1; then
    export IMAGEIO_FFMPEG_EXE="$(command -v ffmpeg)"
  else
    echo "ℹ️ ffmpeg system binary not found; imageio-ffmpeg will download one on first use."
    unset IMAGEIO_FFMPEG_EXE || true
  fi
fi

# ===== 파이썬 의존성 =====
if [ -f /opt/ytshorts/requirements-batch-generate.txt ]; then
  pip install --no-index --find-links=/opt/wheelhouse -r /opt/ytshorts/requirements-batch-generate.txt || \
  pip install -r /opt/ytshorts/requirements-batch-generate.txt
else
  pip install moviepy imageio-ffmpeg openai selenium webdriver-manager tqdm "Pillow<10" pysrt python-dotenv boto3 requests
fi

# ===== 실행 =====
( set -x; python runner.py )
RC=$?

# ===== 세이프가드 (혹시 위가 멈춰도 120분 후 종료) =====
( sleep 7200; shutdown -h now ) >/dev/null 2>&1 &

# ===== 즉시 종료 시도 =====
shutdown -h now || poweroff || halt || true
exit "$RC"