"""Start the local LearnMargin application."""
import argparse
import errno
import socket
import webbrowser

import uvicorn
from dotenv import load_dotenv


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
    args = parser.parse_args()
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
