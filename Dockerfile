FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/app/bridge.db", "/app/bridge_session.session"]

CMD ["python", "main.py"]
