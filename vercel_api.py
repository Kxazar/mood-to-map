import json
from http.server import BaseHTTPRequestHandler


class JsonHandler(BaseHTTPRequestHandler):
    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length)

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_method_not_allowed(self):
        self.send_json({"error": "Method not allowed"}, status=405)

    def respond(self, callback):
        try:
            self.send_json(callback())
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)
