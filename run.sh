docker run --gpus all \
--shm-size 32g \
-p 8000:8000 \
--ipc=host \
-v /data/mineru-unoserver/py/fast_api.py:/usr/local/lib/python3.12/dist-packages/mineru/cli/fast_api.py:ro \
-v /data/mineru-unoserver/py/common.py:/usr/local/lib/python3.12/dist-packages/mineru/cli/common.py:ro \
-v /data/mineru-unoserver/py/unoserver.py:/usr/local/lib/python3.12/dist-packages/mineru/cli/unoserver.py:ro \
-itd mineru-unoserver:latest \
/bin/bash -c "mineru-api --host 0.0.0.0 --port 8000"