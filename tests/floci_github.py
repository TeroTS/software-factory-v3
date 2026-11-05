# Serve only local baseline lookup fixtures; no GitHub, model, or publication requests leave this server.
import http.server
import json


class GitHub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        valid = (
            self.path == "/repos/example/authorized-project/commits/release"
            and self.headers.get("Authorization") == "Bearer local-github-token"
        )
        self.send_response(200 if valid else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"sha": "b" * 40} if valid else {}).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("0.0.0.0", 8080), GitHub).serve_forever()
