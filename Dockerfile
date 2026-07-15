# Maison Monitor — hosted Console image.
# Large by necessity: headless Chromium (post-card rendering + screenshots),
# LibreOffice (QA render loop), CJK fonts (Chinese captions in cards).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    MM_ENV=hosted \
    MM_DATA_DIR=/data \
    PATH="/app/.venv/bin:$PATH"

# System deps: LibreOffice Impress (pptx→pdf for the QA loop), poppler
# (pdftoppm), Noto CJK so card renders show Chinese instead of tofu, gosu to
# drop privileges after fixing volume ownership.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        poppler-utils \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        curl ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# dependency layer (cached until pyproject/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Chromium + its system libraries (required in hosted mode too — card
# rendering runs headless Chromium). Before COPY . . so source-only rebuilds
# don't re-download the browser.
RUN playwright install --with-deps chromium

COPY . .
RUN uv sync --frozen --no-dev

RUN useradd -m -u 10001 mm \
    && mkdir -p /data \
    && chown mm:mm /data \
    # the app tree must be readable by the non-root user regardless of the
    # build context's permission quirks (template decks are read at render)
    && chmod -R a+rX /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["mm", "console", "--no-open"]
