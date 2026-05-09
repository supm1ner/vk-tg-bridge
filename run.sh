#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Создаём venv если нет
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Активируем и устанавливаем зависимости
source venv/bin/activate
pip install --quiet -r requirements.txt

# Запускаем
python main.py
