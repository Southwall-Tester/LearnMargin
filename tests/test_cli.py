import errno
import socket
from unittest.mock import Mock

import pytest
import uvicorn

from learnmargin import cli


@pytest.fixture(autouse=True)
def no_user_environment_or_browser(monkeypatch):
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)
    browser = Mock(return_value=True)
    monkeypatch.setattr(cli.webbrowser, "open", browser)
    return browser


def test_explicit_office_setup_never_binds_or_opens_the_app(monkeypatch):
    setup = Mock()
    monkeypatch.setattr(cli, "setup_office", setup)
    monkeypatch.setattr(cli.socket, "socket", Mock(side_effect=AssertionError("Must not start server")))
    monkeypatch.setattr("sys.argv", ["learnmargin", "--setup-office"])
    cli.main()
    setup.assert_called_once_with()
    assert not cli.webbrowser.open.called


def test_office_setup_uses_bundled_script_and_scrubs_credentials(monkeypatch):
    monkeypatch.setattr(cli, "PLATFORM", "win32")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("WSLENV", "OPENAI_API_KEY")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "ARM64")
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.setup_office()
    command = run.call_args.args[0]
    assert command[-1].endswith("setup_windows_office.ps1")
    environment = run.call_args.kwargs["env"]
    assert "OPENAI_API_KEY" not in environment and "WSLENV" not in environment
    assert environment["PROCESSOR_ARCHITECTURE"] == "ARM64"


def test_office_setup_failure_is_actionable_without_platform_details(monkeypatch, capsys):
    monkeypatch.setattr(cli, "PLATFORM", "win32")
    monkeypatch.setattr(cli.subprocess, "run", Mock(side_effect=OSError("private-path-canary")))
    monkeypatch.setattr("sys.argv", ["learnmargin", "--setup-office"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    message = capsys.readouterr().err
    assert caught.value.code == 1 and "PowerShell" in message
    assert "private-path-canary" not in message


def test_occupied_port_exits_clearly_without_opening_browser_or_starting_server(
        monkeypatch, capsys, no_user_environment_or_browser):
    run = Mock()
    monkeypatch.setattr(cli.LocalServer, "run", run)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as owner:
        owner.bind(("127.0.0.1", 0))
        owner.listen(1)
        port = owner.getsockname()[1]
        monkeypatch.setattr("sys.argv", ["learnmargin", "--port", str(port), "--open"])
        with pytest.raises(SystemExit) as caught:
            cli.main()
        assert caught.value.code == 1
        assert "已被占用" in capsys.readouterr().err
        assert not run.called and not no_user_environment_or_browser.called
        # The existing owner is still listening; the launcher never closes it.
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            accepted, _ = owner.accept()
            accepted.close()


def test_cli_passes_its_bound_socket_without_opening_browser_early(
        monkeypatch, no_user_environment_or_browser):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    captured = []

    def run(server, sockets):
        listener = sockets[0]
        captured.append(listener)
        assert listener.getsockname() == ("127.0.0.1", port)
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        assert server.open_browser and not server.started
        assert not no_user_environment_or_browser.called

    monkeypatch.setattr(cli.LocalServer, "run", run)
    monkeypatch.setattr("sys.argv", ["learnmargin", "--port", str(port), "--open"])
    cli.main()
    assert len(captured) == 1 and captured[0].fileno() == -1
    assert not no_user_environment_or_browser.called


def test_port_permission_failure_is_chinese_and_does_not_open_browser(
        monkeypatch, capsys, no_user_environment_or_browser):
    listener = Mock()
    listener.bind.side_effect = OSError(errno.EACCES, "private platform detail")
    listener.__enter__ = Mock(return_value=listener)
    listener.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(cli.socket, "socket", Mock(return_value=listener))
    monkeypatch.setattr("sys.argv", ["learnmargin", "--port", "12345", "--open"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    message = capsys.readouterr().err
    assert caught.value.code == 1 and "端口权限" in message and "private" not in message
    assert not no_user_environment_or_browser.called


@pytest.mark.parametrize("port", ["0", "65536"])
def test_invalid_port_fails_before_socket_creation(monkeypatch, port, no_user_environment_or_browser):
    create_socket = Mock(side_effect=AssertionError("Must validate before binding"))
    monkeypatch.setattr(cli.socket, "socket", create_socket)
    monkeypatch.setattr("sys.argv", ["learnmargin", "--port", port, "--open"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2 and not create_socket.called
    assert not no_user_environment_or_browser.called


@pytest.mark.parametrize("open_browser", [True, False])
async def test_browser_opens_only_after_real_lifespan_and_listener_start(
        open_browser, no_user_environment_or_browser):
    events = []

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    events.append("application_ready")
                    assert not no_user_environment_or_browser.called
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        config = uvicorn.Config(application, host="127.0.0.1", port=port, lifespan="on", log_level="error")
        config.load()
        server = cli.LocalServer(config, open_browser=open_browser)
        server.lifespan = config.lifespan_class(config)

        def open_url(url):
            assert events == ["application_ready"]
            assert server.started and server.servers[0].is_serving()
            assert url == f"http://127.0.0.1:{port}"
            return True

        no_user_environment_or_browser.side_effect = open_url
        try:
            await server.startup(sockets=[listener])
            assert server.started
            assert no_user_environment_or_browser.call_count == int(open_browser)
        finally:
            if server.started:
                await server.shutdown(sockets=[listener])


async def test_failed_startup_does_not_open_browser(monkeypatch, no_user_environment_or_browser):
    async def failed_startup(_server, sockets=None):
        raise SystemExit(1)

    monkeypatch.setattr(uvicorn.Server, "startup", failed_startup)
    server = cli.LocalServer(uvicorn.Config("learnmargin.app:create_app", factory=True), open_browser=True)
    with pytest.raises(SystemExit):
        await server.startup()
    assert not no_user_environment_or_browser.called


async def test_browser_failure_keeps_successful_server_running(monkeypatch, capsys, no_user_environment_or_browser):
    async def ready(server, sockets=None):
        server.started = True

    monkeypatch.setattr(uvicorn.Server, "startup", ready)
    no_user_environment_or_browser.side_effect = OSError("browser unavailable")
    server = cli.LocalServer(uvicorn.Config("learnmargin.app:create_app", factory=True, port=12345), open_browser=True)
    await server.startup()
    assert server.started and not server.should_exit
    assert "未能自动打开浏览器" in capsys.readouterr().out
