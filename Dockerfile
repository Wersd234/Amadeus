FROM python:3.11-slim

WORKDIR /app

# Set timezone (Melbourne by default, override with TZ env var)
ENV TZ=Australia/Melbourne
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# System libraries required to compile Pillow from source.
# python:3.11-slim does not include these; without them
# `pip install Pillow` fails with a cryptic compiler error.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies in a cache-friendly layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (excluding .dockerignore matches)
COPY . .

CMD ["python", "src/main.py"]