# Check Compose service DNS/database connectivity and a writable bind mount, then serve it over HTTP.
import http.server
import socket
from pathlib import Path


# Use the service name rather than an IP; expose a marker only after database and workspace checks pass.
def main():
    assert Path("from-worker").read_text() == "worker-write"
    with socket.create_connection(("db", 6379), timeout=5) as connection:
        connection.sendall(b"*1\r\n$4\r\nPING\r\n")
        assert connection.recv(64) == b"+PONG\r\n"
    Path("from-container").write_text("dns-db-bind-ok")
    http.server.HTTPServer(
        ("0.0.0.0", 8000), http.server.SimpleHTTPRequestHandler
    ).serve_forever()


if __name__ == "__main__":
    main()
