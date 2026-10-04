"""Start the local LearnMargin application."""
import argparse
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv


def main():
    load_dotenv(".env", override=False)
    parser = argparse.ArgumentParser(description="LearnMargin 本地学习讲义工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1～65535 之间")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run("learnmargin.app:create_app", factory=True, host="127.0.0.1", port=args.port,
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
