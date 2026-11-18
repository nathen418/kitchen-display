FROM python:3.13-slim
RUN pip install --no-cache-dir aiohttp==3.12.15 \
 && useradd --system --uid 10080 --home-dir /app kitchen \
 && mkdir -p /data && chown kitchen /data
WORKDIR /app
COPY app/ /app/
USER kitchen
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=4)"
CMD ["python", "-u", "app.py"]
