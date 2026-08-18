"""The relay spy client, publicizer, and reference spy service.

The list a spy returns is the one piece of this system a player cannot
verify by hand, so these tests hammer the client's validation: malformed
entries are dropped, junk is rejected, nothing is trusted from the network.
"""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from fusionfire.net.spy import (
    MAX_SERVERS,
    PublicizedServer,
    SpyError,
    fetch_servers,
)
from spy import SpyServer, main
from srv import Publicizer


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Recorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.got_message = threading.Event()

    def _message(self, message: dict) -> None:
        self.messages.append(message)
        self.got_message.set()


@pytest.fixture
def spy():
    server = SpyServer("127.0.0.1", _free_port())
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def spy_url(spy):
    return spy.url()


# ----------------------------------------------------------------------
# Validation, the whole point of the client.
# ----------------------------------------------------------------------
def test_fetch_rejects_a_missing_address():
    with pytest.raises(SpyError, match="address"):
        fetch_servers("   ")


def test_fetch_rejects_a_non_http_scheme():
    with pytest.raises(SpyError, match="http"):
        fetch_servers("ftp://example.org/servers")


def test_fetch_rejects_a_reply_that_is_neither_list_nor_object(spy_url, monkeypatch):
    _serve(spy_url, 42, monkeypatch)
    with pytest.raises(SpyError, match="list"):
        fetch_servers(spy_url)


def test_fetch_rejects_a_junk_object(spy_url, monkeypatch):
    _serve(spy_url, {"something": "else"}, monkeypatch)
    with pytest.raises(SpyError, match="list"):
        fetch_servers(spy_url)


def test_fetch_rejects_invalid_json(spy_url, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _body(b"this is not json", monkeypatch))
    with pytest.raises(SpyError, match="JSON"):
        fetch_servers(spy_url)


def test_fetch_drops_bad_entries_and_keeps_good_ones(spy_url, monkeypatch):
    _serve(
        spy_url,
        [
            {"name": "Good", "host": "relay.example.org", "port": 6001},
            {"name": "No port", "host": "relay.example.org"},
            {"name": "Bad port", "host": "relay.example.org", "port": 99999},
            {"name": "No host", "host": "!!!"},  # nothing survives sanitisation
            "not even an object",
            {"host": "spare.example.org", "port": 7000},  # unnamed
        ],
        monkeypatch,
    )
    servers = fetch_servers(spy_url)
    assert [s.host for s in servers] == ["relay.example.org", "spare.example.org"]
    assert servers[0].name == "Good"
    assert servers[1].name == "Publicized relay server", "the unnamed entry got no fallback name"


def test_fetch_caps_the_number_of_entries(spy_url, monkeypatch):
    entries = [{"host": f"relay{i}.example.org", "port": 6001} for i in range(MAX_SERVERS + 50)]
    _serve(spy_url, entries, monkeypatch)
    assert len(fetch_servers(spy_url)) == MAX_SERVERS


def test_a_bare_array_is_accepted(spy_url, monkeypatch):
    _serve(spy_url, [{"host": "relay.example.org", "port": 6001}], monkeypatch)
    assert fetch_servers(spy_url)[0].host == "relay.example.org"


# ----------------------------------------------------------------------
# Against the real reference spy service.
# ----------------------------------------------------------------------
def test_publicize_then_fetch_round_trips(spy_url):
    spy = Publicizer(spy_url=spy_url, name="Test relay", host="relay.example.org", port=6001)
    spy.announce()

    servers = fetch_servers(spy_url)
    assert servers == [PublicizedServer(
        name="Test relay", host="relay.example.org", port=6001,
    )]


def test_delete_removes_a_server(spy_url):
    spy = Publicizer(spy_url=spy_url, name="Test relay", host="relay.example.org", port=6001)
    spy.announce()
    assert fetch_servers(spy_url)

    urllib.request.urlopen(
        f"{spy_url}?host=relay.example.org&port=6001", timeout=10
    ).close()  # a DELETE with no data would not matter here; use DELETE explicitly
    with urllib.request.urlopen(
        urllib.request.Request(
            f"{spy_url}?host=relay.example.org&port=6001",
            method="DELETE",
        ),
        timeout=10,
    ):
        pass
    assert fetch_servers(spy_url) == []


def test_publicized_servers_expire(spy_url, monkeypatch):
    spy = Publicizer(spy_url=spy_url, name="Test relay", host="relay.example.org", port=6001)
    spy.announce()
    assert fetch_servers(spy_url)

    old = time.monotonic
    monkeypatch.setattr("spy.time.monotonic", lambda: old() + 3 * 3600)
    assert fetch_servers(spy_url) == [], "an expired entry should vanish on its own"


def test_a_failing_spy_does_not_break_the_publicizer():
    spy = Publicizer(
        spy_url="http://127.0.0.1:1/servers",
        name="Test relay",
        host="relay.example.org",
        port=6001,
    )
    spy.announce()  # must not raise


# ----------------------------------------------------------------------
# Command line: spy.py <port>
# ----------------------------------------------------------------------
def test_spy_main_serves_on_the_given_port(monkeypatch):
    started = []
    monkeypatch.setattr(SpyServer, "start", lambda self: started.append(self.port))

    def stop_serving(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("spy.time.sleep", stop_serving)

    assert main(["7123"]) == 0
    assert started == [7123]


def test_spy_main_rejects_a_bad_port():
    with pytest.raises(SystemExit):
        main(["70000"])


def test_spy_main_serves_https_when_given_a_certificate(monkeypatch):
    started = []
    monkeypatch.setattr(
        SpyServer,
        "start",
        lambda self: started.append((self.port, self.cert_file, self.key_file)),
    )

    def stop_serving(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("spy.time.sleep", stop_serving)

    assert main(["7123", "--ssl-cert", "cert.pem", "--ssl-key", "key.pem"]) == 0
    assert started == [(7123, "cert.pem", "key.pem")]


def test_spy_main_requires_both_ssl_arguments():
    with pytest.raises(SystemExit):
        main(["7123", "--ssl-cert", "cert.pem"])
    with pytest.raises(SystemExit):
        main(["7123", "--ssl-key", "key.pem"])


def test_serves_https_when_given_a_certificate(tmp_path):
    """A certificate on the command line must turn the service into HTTPS."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "1", "-nodes", "-subj", "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )

    server = SpyServer("127.0.0.1", _free_port(), cert_file=str(cert), key_file=str(key))
    server.start()
    try:
        url = server.url()
        assert url.startswith("https://")

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, context=context, timeout=10) as response:
            payload = json.load(response)
        assert payload == {"servers": []}

        # The same port must not answer plain HTTP: the connection cannot
        # survive the TLS handshake the server insists on.
        with pytest.raises((urllib.error.URLError, OSError)):
            urllib.request.urlopen(
                url.replace("https://", "http://", 1), timeout=10
            )
    finally:
        server.stop()


def test_spy_url_from_config_prefers_the_environment(monkeypatch, tmp_path):
    from srv import spy_url_from_config

    monkeypatch.setenv("FUSION_FIRE_SPY_URL", "https://env.example.org/servers")
    monkeypatch.setattr("srv.config_file", lambda: tmp_path / "settings.json")
    assert spy_url_from_config() == "https://env.example.org/servers"


def test_spy_url_from_config_falls_back_to_the_game_settings(monkeypatch, tmp_path):
    from srv import spy_url_from_config

    monkeypatch.delenv("FUSION_FIRE_SPY_URL", raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"relay_spy_url": "https://settings.example.org/servers"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("srv.config_file", lambda: settings)
    assert spy_url_from_config() == "https://settings.example.org/servers"


def test_spy_url_from_config_is_empty_without_a_config(monkeypatch, tmp_path):
    from srv import spy_url_from_config

    monkeypatch.delenv("FUSION_FIRE_SPY_URL", raising=False)
    monkeypatch.setattr("srv.config_file", lambda: tmp_path / "missing.json")
    assert spy_url_from_config() == ""


# ----------------------------------------------------------------------
# Helpers: tiny fake servers for the validation tests.
# ----------------------------------------------------------------------
def _body(data: bytes, monkeypatch):
    def fake_urlopen(request, timeout=10.0):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit=-1):
                return data

        return Response()

    return fake_urlopen


def _serve(spy_url: str, payload, monkeypatch) -> None:
    body = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", _body(body, monkeypatch))
