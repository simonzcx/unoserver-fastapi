# Use DaoCloud mirrored vllm image for China region for gpu with Ampere architecture and above (Compute Capability>=8.0)
# Compute Capability version query (https://developer.nvidia.com/cuda-gpus)
FROM docker.m.daocloud.io/vllm/vllm-openai:v0.11.0

# ===========================================
# unoconv 相关配置
# ===========================================
# renovate: pypi: unoserver
ARG VERSION_UNOSERVER=3.4

# ===========================================
# 镜像元数据
# ===========================================
LABEL org.opencontainers.image.title="mineru-unoserver"
LABEL org.opencontainers.image.description="Container image that contains mineru and unoserver with libreoffice for file format conversions"

# ===========================================
# 系统依赖安装
# ===========================================
RUN apt-get update && \
    apt-get install -y \
        # mineru 依赖：中文字体支持
        fonts-noto-core \
        fonts-noto-cjk \
        fontconfig \
        # mineru 依赖：OpenCV 支持
        libgl1 \
        # unoconv 依赖：额外字体支持
        fonts-noto-extra \
        # unoconv 依赖：LibreOffice 主程序
        libreoffice \
        # unoconv 依赖：网络工具
        net-tools \
        # unoconv 依赖：中文locale支持
        locales \
        # unoconv 依赖：更多字体支持
        fonts-noto-hinted \
        fonts-noto-unhinted \
        fonts-dejavu \
        fonts-freefont-ttf \
        fonts-liberation && \
    # 设置中文locale
    sed -i '/zh_CN.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen && \
    # 创建中文字体目录结构
    mkdir -p /usr/share/fonts/chinese && \
    # 设置locale环境变量
    echo 'export LANG=zh_CN.UTF-8' > /etc/profile.d/locale.sh && \
    echo 'export LC_ALL=zh_CN.UTF-8' >> /etc/profile.d/locale.sh && \
    echo 'export LC_CTYPE=zh_CN.UTF-8' >> /etc/profile.d/locale.sh && \
    chmod +x /etc/profile.d/locale.sh && \
    # 更新字体缓存
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# ===========================================
# mineru 相关配置
# ===========================================
# 安装 mineru 最新版本
RUN python3 -m pip install -U 'mineru[core]' -i https://mirrors.aliyun.com/pypi/simple --break-system-packages && \
    python3 -m pip cache purge

# 下载模型并更新配置文件
RUN /bin/bash -c "mineru-models-download -s modelscope -m all"

# ===========================================
# unoconv 相关配置
# ===========================================
# 安装 unoserver
RUN python3 -m pip install --break-system-packages -U unoserver==${VERSION_UNOSERVER}

# ===========================================
# 用户和权限配置
# ===========================================
# 设置中文环境变量
ENV LANG=zh_CN.UTF-8
ENV LC_ALL=zh_CN.UTF-8
ENV LC_CTYPE=zh_CN.UTF-8

# 保持与原mineru-dockerfile.yml一致，默认使用root用户，确保GPU访问权限
# 原配置没有指定USER，默认使用root用户

# ===========================================
# 容器运行配置
# ===========================================
# 数据卷配置
VOLUME ["/data"]
# unoconv 端口暴露
EXPOSE 2003

# 设置入口点，与原mineru-dockerfile.yml保持一致
# 确保每次运行命令时都设置MINERU_MODEL_SOURCE=local环境变量
ENTRYPOINT ["/bin/bash", "-c", "export MINERU_MODEL_SOURCE=local && exec \"$@\"", "--"]