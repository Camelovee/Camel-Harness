"""最小本地 JSON-RPC Server，验证真实 stdio 连接而非模拟 SDK 生命周期。"""

import json
import os
import sys
import time
from pathlib import Path

mode, pid_path = sys.argv[1:3]
Path(pid_path).write_text(str(os.getpid()), encoding="utf-8")
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request or mode == "hang":
        continue
    method = request["method"]
    response = {"jsonrpc": "2.0", "id": request["id"]}
    if method == "initialize":
        response["result"] = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test-server", "version": "1"},
        }
    elif method == "tools/list" and mode == "fail":
        response["error"] = {"code": -32603, "message": "发现工具失败"}
    elif method == "tools/list":
        response["result"] = {
            "tools": [
                {
                    "name": "ping",
                    "description": "返回测试文本",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    else:
        response["result"] = {"content": [{"type": "text", "text": "pong"}]}
    print(json.dumps(response), flush=True)
if mode == "slow":
    Path(pid_path + ".closing").touch()
    time.sleep(0.3)
