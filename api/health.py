import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import llm_config, safe_endpoint_label
from vercel_api import JsonHandler


class handler(JsonHandler):
    def do_GET(self):
        def payload():
            config = llm_config()
            return {
                "ok": True,
                "kimi": config["enabled"],
                "provider": config["provider"],
                "model": config["model"],
                "endpoint": safe_endpoint_label(config["base_url"]),
            }

        self.respond(payload)

    def do_POST(self):
        self.send_method_not_allowed()
