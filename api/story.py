import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import handle_story
from vercel_api import JsonHandler


class handler(JsonHandler):
    def do_GET(self):
        self.respond(lambda: handle_story(urlparse(self.path).query))

    def do_POST(self):
        self.send_method_not_allowed()
