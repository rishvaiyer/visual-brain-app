# Visual Brain, hosted.
#
# Same app as on your own machine, in a Linux container. It binds 0.0.0.0 and
# refuses to start unless you set either VISUAL_BRAIN_PASSPHRASE (private board)
# or VISUAL_BRAIN_DEMO=1 (public playground that wipes itself on a timer).
# Mac-only extras (Apple Vision labels, local CLIP search) are simply absent.
#
# The image carries no board data. `data/` is excluded in .dockerignore; the
# runtime directory is /data, so a hosted instance starts empty.

FROM python:3.12-slim

# gosu drops privileges in the entrypoint once the volume has been chowned.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gosu \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code only. Anything matching .dockerignore, data/ above all, stays out.
COPY *.py ./
COPY *.html *.css *.js favicon.svg ./
COPY docker-entrypoint.sh ./

ENV PYTHONUNBUFFERED=1 \
    VISUAL_BRAIN_HOST=0.0.0.0 \
    VISUAL_BRAIN_DATA_DIR=/data

RUN chmod +x docker-entrypoint.sh \
 && useradd --create-home --uid 10001 brain \
 && mkdir -p /data \
 && chown -R brain:brain /app

EXPOSE 8777
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os,urllib.request,sys; \
url='http://127.0.0.1:'+(os.environ.get('VISUAL_BRAIN_PORT') or os.environ.get('PORT') or '8777')+'/healthz'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).status==200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "server.py"]
