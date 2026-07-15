import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fusion-addin"))

from FusionAIModeler.server.request_security import (  # noqa: E402
    is_allowed_origin,
    is_json_content_type,
    is_json_rpc_request,
    is_loopback_host,
)


class MCPRequestSecurityTests(unittest.TestCase):
    def test_accepts_explicit_loopback_hosts(self):
        self.assertTrue(is_loopback_host("127.0.0.1:9100"))
        self.assertTrue(is_loopback_host("localhost:9100"))
        self.assertTrue(is_loopback_host("[::1]:9100"))

    def test_rejects_non_loopback_hosts_and_origins(self):
        self.assertFalse(is_loopback_host("example.com"))
        self.assertFalse(is_allowed_origin("https://example.com"))
        self.assertFalse(is_allowed_origin("null"))
        self.assertTrue(is_allowed_origin(None))
        self.assertTrue(is_allowed_origin("http://127.0.0.1:3000"))

    def test_requires_application_json(self):
        self.assertTrue(is_json_content_type("application/json"))
        self.assertTrue(is_json_content_type("application/json; charset=utf-8"))
        self.assertFalse(is_json_content_type("text/plain"))
        self.assertFalse(is_json_content_type(None))

    def test_requires_json_rpc_2_object(self):
        self.assertTrue(is_json_rpc_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
        self.assertFalse(is_json_rpc_request([{"jsonrpc": "2.0"}]))
        self.assertFalse(is_json_rpc_request({"jsonrpc": "1.0"}))

if __name__ == "__main__":
    unittest.main()
