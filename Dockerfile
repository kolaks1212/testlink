FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (optional but useful)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create temp directory
RUN mkdir -p /tmp/bot_downloads

CMD ["python", "bot.py"]
