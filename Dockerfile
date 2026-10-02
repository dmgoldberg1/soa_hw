FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN addgroup -S app && adduser -S -G app app
COPY --chown=app:app services/orders/main.py ./main.py
USER app

EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=3 \
    CMD ["python", "-c", "import urllib.request; r = urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2); raise SystemExit(0 if r.status == 200 else 1)"]

CMD ["python", "main.py"]
