#!/bin/bash
set -e -u

# ===========================================
# unoconv 相关配置 - 函数定义
# ===========================================
# 等待 unoserver 启动的函数
wait_for_unoserver() {
    echo "正在等待 unoserver 在端口 2003 上启动..."
    while ! netstat -tln | grep -q 2003; do
        sleep 1
    done
    echo "unoserver 已启动."
}

# ===========================================
# 环境变量配置
# ===========================================
# 设置命令行提示符
export PS1='\u@\h:\w\$ '
# mineru 相关：设置模型源为本地
export MINERU_MODEL_SOURCE=local

# ===========================================
# 启动信息打印
# ===========================================
# 打印 unoconv 相关：LibreOffice 版本
echo "使用的 LibreOffice 版本: $(libreoffice --version)"
# 打印 mineru 相关：mineru 版本
echo "已安装的 mineru 版本: $(pip show mineru | grep Version | cut -d' ' -f2)"
# 打印 unoconv 相关：unoconv 版本
if command -v unoconv &> /dev/null; then
    echo "已安装的 unoconv 版本: $(unoconv --version)"
else
    echo "unoconv 命令未找到，但 unoserver 可能已安装"
fi
# 打印 unoconv 相关：unoserver 版本
if command -v unoserver &> /dev/null; then
    echo "已安装的 unoserver 版本: $(unoserver --version)"
else
    echo "unoserver 命令未找到"
fi

# ===========================================
# 容器运行模式判断
# ===========================================
# 如果不是交互式终端，则以非交互式模式运行
if [ ! -t 0 ]; then
    echo "以非交互式模式运行 mineru-unoserver."
    echo "如需交互式模式，请使用 '-it' 参数，例如: 'docker run -v /tmp:/data -it mineru-unoserver'."

    # ===========================================
    # unoconv 相关：启动服务
    # ===========================================
    # 直接启动 unoserver（非后台模式，保持容器运行）
    unoserver --interface 0.0.0.0
else
    # 交互式模式运行
    echo "以交互式模式运行 mineru-unoserver."
    echo "如需非交互式模式，请省略 '-it' 参数，例如: 'docker run -p 2003:2003 mineru-unoserver'."
    echo "使用 'mineru' 命令与 mineru 交互，使用 'unoconv' 或 'unoserver' 进行文档转换."

    # ===========================================
    # unoconv 相关：启动服务
    # ===========================================
    # 在后台启动 unoserver
    unoserver --interface 0.0.0.0 &
    UNOSERVER_PID=$!

    # 等待 unoserver 启动完成
    wait_for_unoserver

    # ===========================================
    # 命令执行或终端启动
    # ===========================================
    # 如果传入了命令，则执行命令并退出，否则启动 bash
    if [[ $# -gt 0 ]]; then
        eval "$@"
        # 杀死后台运行的 unoserver
        kill $UNOSERVER_PID
    else
        /bin/bash
        # 杀死后台运行的 unoserver
        kill $UNOSERVER_PID
    fi
fi