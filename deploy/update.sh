#!/bin/sh
# Автообновление: раз в 10 минут проверяет GitHub и пересобирает бота, если код изменился.
cd /opt/noxen || exit 1
git fetch -q origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git reset -q --hard origin/main
  docker compose up -d --build
  docker image prune -f >/dev/null
  echo "$(date -Is) updated to $(git rev-parse --short HEAD)"
fi
