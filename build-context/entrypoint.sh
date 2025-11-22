#!/bin/bash
set -e -u

# Function to wait for unoserver to start with timeout
wait_for_unoserver() {
    echo "Waiting for unoserver to start on port 2003..."
    local timeout=30
    local counter=0
    while ! netstat -tln | grep -q 2003; do
        sleep 1
        counter=$((counter + 1))
        if [ $counter -ge $timeout ]; then
            echo "Error: Timeout waiting for unoserver to start on port 2003"
            exit 1
        fi
    done
    echo "unoserver started."
}

export PS1='\u@\h:\w\$ '

echo "using: $(libreoffice --version)"

# if tty then assume that container is interactive
if [ ! -t 0 ]; then
    echo "Running unoserver-docker in non-interactive."
    echo "For interactive mode use '-it', e.g. 'docker run -v /tmp:/data -it unoserver/unoserver-docker'."

    # Start unoserver in the background
    unoserver --interface 0.0.0.0 --daemon &

    # Wait for unoserver to start
    wait_for_unoserver

    # Start FastAPI application with uvicorn
    echo "Starting FastAPI application..."
    uvicorn app:app --host 0.0.0.0 --port 8000 --reload=False
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
    uvicorn app:app --host 0.0.0.0 --port 8000 --reload=False &
    
    # if commands have been passed to container run them and exit, else start bash
    if [[ $# -gt 0 ]]; then
        eval "$@"
    else
        /bin/bash
    fi
fi
