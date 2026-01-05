#!/bin/bash
set -e -u

# Function to wait for unoserver to start with timeout
wait_for_unoserver() {
    echo "Waiting for unoserver to start on port 2003..."
    local timeout=60
    local counter=0

    # 等待端口开放
    while ! netstat -tln | grep -q 2003; do
        sleep 1
        counter=$((counter + 1))
        if [ $counter -ge $timeout ]; then
            echo "Error: Timeout waiting for unoserver to start on port 2003"
            exit 1
        fi
    done

    # 额外等待 unoserver 完全初始化
    echo "Port 2003 is open, waiting additional 10 seconds for unoserver to be ready..."
    sleep 10

    # 尝试进行简单连接测试
    echo "Testing unoserver readiness..."
    local test_counter=0
    while [ $test_counter -lt 10 ]; do
        if echo -e "\x00\x00\x00\x00" | nc localhost 2003 2>/dev/null; then
            echo "unoserver is ready."
            return 0
        fi
        sleep 2
        test_counter=$((test_counter + 1))
    done

    echo "Warning: Could not confirm unoserver readiness, but port is open"
}

export PS1='\u@\h:\w\$ '

echo "using: $(libreoffice --version)"

# if tty then assume that container is interactive
if [ ! -t 0 ]; then
    echo "Running unoserver-docker in non-interactive."
    echo "For interactive mode use '-it', e.g. 'docker run -v /tmp:/data -it unoserver/unoserver-docker'."

    # Start unoserver in the background
    unoserver --interface 0.0.0.0 --port 2003 --daemon &

    # Wait for unoserver to start
    wait_for_unoserver

    # Start FastAPI application with uvicorn
    echo "Starting FastAPI application..."
    uvicorn app:app --host 0.0.0.0 --port 8000
    # Keep container running if uvicorn exits unexpectedly
    tail -f /dev/null
else
    echo "Running unoserver-docker in interactive mode."
    echo "For non-interactive mode omit '-it', e.g. 'docker run -p 2003:2003 unoserver/unoserver-docker'."

    # default parameters for supervisord
    export SUPERVISOR_INTERACTIVE_CONF='/supervisor/conf/interactive/supervisord.conf'
    export UNIX_HTTP_SERVER_PASSWORD=${UNIX_HTTP_SERVER_PASSWORD:-$(cat /proc/sys/kernel/random/uuid)}

    # run supervisord as detached
    supervisord -c "$SUPERVISOR_INTERACTIVE_CONF"

    # wait until unoserver started and listens on port 2003.
    wait_for_unoserver

    # Start FastAPI application with uvicorn in background
    echo "Starting FastAPI application in background..."
    uvicorn app:app --host 0.0.0.0 --port 8000 &

    # if commands have been passed to container run them and exit, else start bash
    if [[ $# -gt 0 ]]; then
        eval "$@"
    else
        /bin/bash
    fi
fi