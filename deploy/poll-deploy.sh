#!/usr/bin/env bash
# Poll-based auto-deploy for the Pi — a lightweight alternative to the
# self-hosted GitHub Actions runner (.github/workflows/deploy.yml). Run it from
# cron every couple of minutes; it fetches main and redeploys only when the
# remote has actually moved. No extra software to install on the Pi.
#
# See "Continuous deployment" in INSTALL.md for setup (cron + linger).
set -euo pipefail

cd "$HOME/pillbox"
git fetch -q origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    git reset --hard -q origin/main
    # cron has no login session, so give systemctl --user its session bus
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    systemctl --user restart pillbox
    echo "$(date '+%Y-%m-%d %H:%M:%S') deployed $(git rev-parse --short HEAD)"
fi
