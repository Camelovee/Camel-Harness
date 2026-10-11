"""创建真实后代，供超时和取消测试确认整棵进程树退出。"""

import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
Path("child.pid").write_text(str(child.pid), encoding="utf-8")
time.sleep(30)
