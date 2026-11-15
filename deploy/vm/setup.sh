#!/bin/sh
# Set up GigBid's backend on a fresh Ubuntu VM, then start it. Run from the cloned repository:
#   sh deploy/vm/setup.sh
# Safe to run again: it skips what is done and rebuilds after a `git pull`.
set -eu

cd "$(dirname "$0")/../.."

if ! command -v docker >/dev/null 2>&1; then
  echo "== Installing Docker from Ubuntu's packages"
  sudo apt-get update -q
  sudo apt-get install -y -q docker.io docker-compose-v2 git
  sudo usermod -aG docker "$USER"
fi

# Small VMs build and run more reliably with some swap.
if [ "$(swapon --show | wc -l)" -eq 0 ] && [ "$(awk '/MemTotal/ {print $2}' /proc/meminfo)" -lt 7000000 ]; then
  echo "== Adding 4 GB of swap"
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Fill in the values listed in docs/DEPLOYMENT.md,"
  echo "then run this script again:  nano .env"
  exit 1
fi
for name in GROQ_API_KEY TOKEN_ENCRYPTION_KEY MCP_TENANT_SIGNING_KEY DEMO_EMAIL DEMO_PASSWORD API_DOMAIN; do
  value=$(grep -E "^$name=" .env | head -1 | cut -d= -f2-)
  if [ -z "$value" ] || [ "$value" = "replace_me" ]; then
    echo "Set $name in .env first (see docs/DEPLOYMENT.md)."
    exit 1
  fi
done

echo "== Building and starting the backend (the first build takes several minutes)"
sudo docker compose -f infra/docker-compose.yml -f deploy/vm/compose.vm.yml --env-file .env \
  up -d --build seed devsim marketplace portfolio workspace api worker caddy

echo "== Done. Check the API:  curl https://$(grep -E '^API_DOMAIN=' .env | cut -d= -f2-)/health"
