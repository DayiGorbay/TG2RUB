#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="TG2RUB"
SERVICE_NAME="tg2rub"
INSTALL_DIR="/opt/TG2RUB"
REPO_URL="https://github.com/DayiGorbay/TG2RUB.git"
PROJECT_ROOT="${INSTALL_DIR}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$(whoami)}"

echo "==== ${PROJECT_NAME} Installer ===="
echo "Project: ${PROJECT_NAME}"
echo "Target Path: ${PROJECT_ROOT}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root: sudo bash install.sh"
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  echo "Installing system prerequisites..."
  apt-get update -y
  apt-get install -y python3 python3-venv python3-pip git
fi

if [ ! -d "${PROJECT_ROOT}" ]; then
  mkdir -p "${PROJECT_ROOT}"
fi

if [ ! -f "${PROJECT_ROOT}/requirements.txt" ]; then
  echo "Project files not found in ${PROJECT_ROOT}. Cloning repository..."
  rm -rf "${PROJECT_ROOT}"
  git clone "${REPO_URL}" "${PROJECT_ROOT}"
else
  echo "Project files found in ${PROJECT_ROOT}. Using existing files."
fi

cd "${PROJECT_ROOT}"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Error: Python 3 not found. Please install Python 3.10+ and re-run."
  exit 1
fi

if [ ! -f "requirements.txt" ]; then
  echo "Error: requirements.txt not found in project root."
  exit 1
fi

if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  "${PYTHON_BIN}" -m venv venv
else
  echo "Virtual environment already exists. Skipping create."
fi

VENV_PYTHON="venv/bin/python"
if [ ! -x "${VENV_PYTHON}" ]; then
  echo "Error: virtual environment python not found at ${VENV_PYTHON}"
  exit 1
fi

echo "Upgrading pip..."
"${VENV_PYTHON}" -m pip install --upgrade pip

echo "Installing dependencies..."
"${VENV_PYTHON}" -m pip install -r requirements.txt

echo
echo "Configure environment values:"
read -r -p "API_ID: " API_ID
while [ -z "${API_ID}" ]; do
  echo "API_ID cannot be empty."
  read -r -p "API_ID: " API_ID
done

read -r -p "API_HASH: " API_HASH
while [ -z "${API_HASH}" ]; do
  echo "API_HASH cannot be empty."
  read -r -p "API_HASH: " API_HASH
done

read -r -p "BOT_TOKEN: " BOT_TOKEN
while [ -z "${BOT_TOKEN}" ]; do
  echo "BOT_TOKEN cannot be empty."
  read -r -p "BOT_TOKEN: " BOT_TOKEN
done

read -r -p "ADMIN_TELEGRAM_ID (numeric): " ADMIN_TELEGRAM_ID
while ! [[ "${ADMIN_TELEGRAM_ID}" =~ ^[0-9]+$ ]]; do
  echo "ADMIN_TELEGRAM_ID must be numeric."
  read -r -p "ADMIN_TELEGRAM_ID (numeric): " ADMIN_TELEGRAM_ID
done

read -r -p "BALE_BOT_TOKEN: " BALE_BOT_TOKEN
while [ -z "${BALE_BOT_TOKEN}" ]; do
  echo "BALE_BOT_TOKEN cannot be empty."
  read -r -p "BALE_BOT_TOKEN: " BALE_BOT_TOKEN
done

read -r -p "BALE_ADMIN_CHAT_ID: " BALE_ADMIN_CHAT_ID
while [ -z "${BALE_ADMIN_CHAT_ID}" ]; do
  echo "BALE_ADMIN_CHAT_ID cannot be empty."
  read -r -p "BALE_ADMIN_CHAT_ID: " BALE_ADMIN_CHAT_ID
done

read -r -p "RUBIKA_SESSION [rubsession]: " RUBIKA_SESSION
RUBIKA_SESSION="${RUBIKA_SESSION:-rubsession}"

cat > .env <<EOF
API_ID=${API_ID}
API_HASH=${API_HASH}
BOT_TOKEN=${BOT_TOKEN}
ADMIN_TELEGRAM_ID=${ADMIN_TELEGRAM_ID}
BALE_BOT_TOKEN=${BALE_BOT_TOKEN}
BALE_ADMIN_CHAT_ID=${BALE_ADMIN_CHAT_ID}
RUBIKA_SESSION=${RUBIKA_SESSION}
EOF
echo ".env file generated successfully."

mkdir -p "downloads" "downloads/url" "queue"
chown -R "${RUN_USER}:${RUN_USER}" "${PROJECT_ROOT}"

echo "Creating systemd service: ${SERVICE_NAME}"
cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=TG2RUB Telegram to Rubika bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${PROJECT_ROOT}
ExecStart=${PROJECT_ROOT}/venv/bin/python ${PROJECT_ROOT}/main.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo
echo "Installation complete for ${PROJECT_NAME}."
echo "Service installed and started: ${SERVICE_NAME}"
echo "Useful commands:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
