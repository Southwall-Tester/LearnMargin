"""Start the local LearnMargin application."""
import argparse
import errno
import os
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

PLATFORM = sys.platform


def setup_office() -> None:
    """An explicit local command, never triggered by an upload or HTTP request."""
    if PLATFORM != "win32":
        raise ValueError("此配置命令用于 Windows；Linux 请安装系统 LibreOffice 和 bubblewrap。")
    from .extraction_worker import worker_environment

    package = Path(__file__).resolve().parent
    script = package / "resources" / "setup_windows_office.ps1"
    if not script.is_file():
        script = package.parents[1] / "scripts" / "setup_windows_office.ps1"
    if not script.is_file():
        raise ValueError("安装包缺少 Office 沙箱配置脚本，请重新安装 LearnMargin。")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = worker_environment()
    for key in ("PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"):
        if key in os.environ:
            environment[key] = os.environ[key]
    try:
        completed = subprocess.run(
            [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            env=environment, check=False,
        )
    except OSError:
        raise ValueError("无法启动配置脚本，请检查 Windows PowerShell 与 WSL 2。") from None
    if completed.returncode:
        raise ValueError("Office 沙箱配置未完成，请按上方提示处理后重试。")


class LocalServer(uvicorn.Server):
    """Open the browser only after this server's lifespan and listener are ready."""

    def __init__(self, config: uvicorn.Config, *, open_browser: bool = False):
        super().__init__(config)
        self.open_browser = open_browser

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started and not self.should_exit:
            url = f"http://127.0.0.1:{self.config.port}"
            print(f"LearnMargin 已启动：{url}", flush=True)
            if self.open_browser:
                try:
                    if not webbrowser.open(url):
                        print(f"未能自动打开浏览器，请访问：{url}", flush=True)
                except (webbrowser.Error, OSError):
                    print(f"未能自动打开浏览器，请访问：{url}", flush=True)


def main():
    load_dotenv(".env", override=False)
    parser = argparse.ArgumentParser(description="LearnMargin 本地学习讲义工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--setup-office", action="store_true", help="配置 Windows 旧 Office 转换沙箱")
    args = parser.parse_args()
    if args.setup_office:
        try:
            setup_office()
        except ValueError as error:
            parser.exit(1, f"{error}\n")
        return
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1～65535 之间")
    # Keep the bound socket until shutdown: a preflight check followed by closing
    # it would let another process claim the port before Uvicorn starts.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", args.port))
            listener.listen(2048)
        except OSError as error:
            if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048:
                parser.exit(1, f"端口 {args.port} 已被占用，本次未启动。请关闭旧服务后再启动，"
                              "或使用 --port 指定其他端口。\n")
            parser.exit(1, f"无法监听本机端口 {args.port}，本次未启动。请检查端口权限或使用 --port 指定其他端口。\n")
        config = uvicorn.Config("learnmargin.app:create_app", factory=True, host="127.0.0.1", port=args.port,
                                workers=1, log_level="info", access_log=False)
        LocalServer(config, open_browser=args.open).run(sockets=[listener])


if __name__ == "__main__":
    main()
