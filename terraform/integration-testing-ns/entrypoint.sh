#!/bin/bash
set -e

python3 -m venv /app-venv

# Activate virtual environment
source /app-venv/bin/activate

pip install Flask==3.1.3 kubernetes==37.0.1

if [ ! -f /app-venv/bin/vcluster ]; then
    curl -L -o /app-venv/bin/vcluster "https://github.com/loft-sh/vcluster/releases/latest/download/vcluster-linux-amd64"
    chmod +x /app-venv/bin/vcluster
fi

if [ ! -f /app-venv/bin/helm ]; then
    wget https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz
    tar -zxvf helm-v4.3.1-linux-amd64.tar.gz
    mv linux-amd64/helm /app-venv/bin/helm
    chmod +x /app-venv/bin/helm
fi

# Run the server
export PYTHONUNBUFFERED=1
exec python /app/api.py
