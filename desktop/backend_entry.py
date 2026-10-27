"""PyInstaller 的桌面后端入口，保留标准输入输出用于父进程通信。"""

from camel_backend.desktop import main

if __name__ == "__main__":
    main()
