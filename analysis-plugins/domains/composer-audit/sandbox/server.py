"""Internal, serial CLI worker. Deliberately has no credentials or shared tenant storage."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from audit import audit

MAX_INPUT = 24 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass  # Request data and Composer output may contain repository credentials.

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/audit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_INPUT:
                self.send_error(413)
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            result = audit(payload)
        except (ValueError, TypeError):
            self.send_error(400)
            return
        encoded = json.dumps(result).encode()
        if len(encoded) > 2 * 1024 * 1024:
            encoded = b'{"findings":[],"diagnostics":["output-limit: audit result exceeded its size limit"]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    # One child at a time: its input directory is removed before the next tenant is read.
    HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
