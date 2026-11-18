# ASTRA Code Arena: one image, two containers (web + judge). See docker-compose.yml.
FROM python:3.12-slim
# Extra judge languages (C, C++ and Python are always installed). Remove what you don't need to shrink the image.
ARG EXTRA_LANGS="java node"
RUN set -e; pkgs="gcc g++ libc6-dev tesseract-ocr tesseract-ocr-eng sqlite3"; \
    case " $EXTRA_LANGS " in *" java "*) pkgs="$pkgs default-jdk-headless";; esac; \
    case " $EXTRA_LANGS " in *" node "*) pkgs="$pkgs nodejs";; esac; \
    case " $EXTRA_LANGS " in *" go "*) pkgs="$pkgs golang-go";; esac; \
    case " $EXTRA_LANGS " in *" rust "*) pkgs="$pkgs rustc";; esac; \
    apt-get update && apt-get install -y --no-install-recommends $pkgs && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY admin ./admin
COPY tests ./tests
ENV DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["python", "-m", "app.main"]
