FROM eclipse-temurin:24.0.1_9-jdk-alpine-3.21

ARG BUILD_CONTEXT="build-context"
ARG UID=worker
ARG GID=worker
# renovate: pypi: unoserver
ARG VERSION_UNOSERVER=3.4

LABEL org.opencontainers.image.title="unoserver-docker"
LABEL org.opencontainers.image.description="Container image that contains unoserver and libreoffice including large set of fonts for file format conversions"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.documentation="https://github.com/unoconv/unoserver-docker/blob/main/README.adoc"
LABEL org.opencontainers.image.source="https://github.com/unoconv/unoserver-docker"
LABEL org.opencontainers.image.url="https://github.com/unoconv/unoserver-docker"

WORKDIR /

RUN addgroup -S ${GID} && adduser -S ${UID} -G ${GID}

RUN apk add --no-cache \
    bash curl \
    py3-pip \
    libreoffice \
    supervisor

# fonts - https://wiki.alpinelinux.org/wiki/Fonts
RUN apk add --no-cache \
    font-noto font-noto-cjk font-noto-extra \
    terminus-font \
    ttf-font-awesome \
    ttf-dejavu \
    ttf-freefont \
    ttf-hack \
    ttf-inconsolata \
    ttf-liberation \
    ttf-mononoki \
    ttf-opensans \
    fontconfig && \
    # Create font directory structure
    mkdir -p /usr/share/fonts/chinese && \
    # Install packages needed for Chinese support
    apk add --no-cache \
    musl-locales musl-locales-lang && \
    # Set locale environment variables for container
    echo 'export LANG=zh_CN.UTF-8' > /etc/profile.d/locale.sh && \
    echo 'export LC_ALL=zh_CN.UTF-8' >> /etc/profile.d/locale.sh && \
    echo 'export LC_CTYPE=zh_CN.UTF-8' >> /etc/profile.d/locale.sh && \
    chmod +x /etc/profile.d/locale.sh && \
    # Update font cache
    fc-cache -f

RUN rm -rf /var/cache/apk/* /tmp/*

# https://github.com/unoconv/unoserver/
RUN pip install --break-system-packages -U unoserver==${VERSION_UNOSERVER} fastapi uvicorn python-multipart

# setup supervisor
COPY --chown=${UID}:${GID} app.py /home/worker/
COPY --chown=${UID}:${GID} ${BUILD_CONTEXT} /
RUN chmod +x /entrypoint.sh && \
    chown -R ${UID}:0 /run && \
    chmod -R g=u /run

USER ${UID}
WORKDIR /home/worker
ENV HOME="/home/worker"

VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]