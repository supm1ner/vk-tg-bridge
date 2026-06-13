#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env created from .env.example — edit it with your credentials"
    exit 1
fi

if [ ! -d venv ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -U pip
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

exec python main.py
