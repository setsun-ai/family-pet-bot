#!/usr/bin/env bash
# Run the bot 24/7 on a Raspberry Pi or any Linux server with systemd.
# From the project folder, as a normal user (NOT with sudo):
#     bash deploy/linux/install-service.sh            install / update and (re)start
#     bash deploy/linux/install-service.sh --remove   stop and remove the service
set -euo pipefail
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
USER_NAME="$(id -un)"
UNITS=(family-pet-bot.service family-pet-bot-backup.service family-pet-bot-backup.timer)

if [ "$(id -u)" -eq 0 ]; then echo "Run as a normal user; the script asks for sudo itself." >&2; exit 1; fi

if [ "${1:-}" = "--remove" ]; then
    sudo systemctl disable --now family-pet-bot.service family-pet-bot-backup.timer 2>/dev/null || true
    for unit in "${UNITS[@]}"; do sudo rm -f "/etc/systemd/system/$unit"; done
    sudo systemctl daemon-reload
    echo "Removed. Your data (.env, data/, backups/) is untouched."
    exit 0
fi

echo "==> Project: $DIR (user: $USER_NAME)"
if command -v apt-get >/dev/null; then
    echo "==> System packages + automatic security updates"
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-venv python3-pip unattended-upgrades >/dev/null
    printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
        | sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null
fi

echo "==> Python environment (.venv)"
[ -x "$DIR/.venv/bin/python" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"

echo "==> Checking .env"
if ! "$DIR/.venv/bin/python" -m petbot check-config; then
    echo "Fix .env first (or run: .venv/bin/python -m petbot setup), then run this script again." >&2
    exit 1
fi

echo "==> systemd"
for unit in "${UNITS[@]}"; do
    sed -e "s|@DIR@|$DIR|g" -e "s|@USER@|$USER_NAME|g" "$DIR/deploy/linux/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable family-pet-bot.service family-pet-bot-backup.timer >/dev/null
sudo systemctl restart family-pet-bot.service
sudo systemctl start family-pet-bot-backup.timer

echo
echo "Running. First start? Read the one-time owner link in the log:"
echo "    journalctl -u family-pet-bot -n 30 --no-pager"
echo "Live log:  journalctl -u family-pet-bot -f"
echo "Restart after editing .env:  sudo systemctl restart family-pet-bot"
