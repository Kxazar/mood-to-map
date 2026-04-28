import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import handle_attraction_copy
from vercel_api import JsonHandler


class handler(JsonHandler):
    def do_POST(self):
        self.respond(lambda: handle_attraction_copy(self.read_body()))

    def do_GET(self):
        self.send_method_not_allowed()
