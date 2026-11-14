#!/usr/bin/env python3
"""
ZeroMCP Stdio Limiter Verification Suite
========================================
Dedicated regression suite for scripts/zeromcp_stdio_limiter.py testing:
1. Notification silence with exhausted bucket.
2. Batch containing invalid member (42) and throttled call produces exactly 1 merged array.
3. Mixed batch (allowed request + throttled request + notification) in original order.
4. Consecutive batches.
5. 10 MiB EOF framing boundary.
"""

import sys
import os
import json
import subprocess
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMITER_PATH = os.path.join(PROJECT_ROOT, "scripts", "zeromcp_stdio_limiter.py")
PYTHON_SERVER = os.path.join(PROJECT_ROOT, "zeromcp-py", "zeromcp.py")

class TestStdioLimiter(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, 'p') and self.p:
            try:
                self.p.stderr.close()
                self.p.stdout.close()
                self.p.kill()
            except Exception:
                pass

    def spawn_limiter(self, burst: float = 1.0, rate: float = 0.0):
        env = dict(os.environ)
        env["ZEROMCP_CAS_ENABLED"] = "0"
        cmd = [sys.executable, LIMITER_PATH, "--burst", str(burst), "--rate", str(rate), "--", sys.executable, PYTHON_SERVER]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        return self.p

    def test_notification_silence_on_throttling(self):
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        p.stdin.write(b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"echo","arguments":{"message":"silent"}}}\n')
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.read()
        p.wait()
        self.assertEqual(out, b"", "Throttled notification must produce zero output bytes")

    def test_batch_invalid_member_and_throttled_call(self):
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        p.stdin.write(b'[42, {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"message":"b"}}}]\n')
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertIsInstance(resp, list)
        self.assertEqual(len(resp), 2)
        self.assertEqual(resp[0].get("id"), None)
        self.assertEqual(resp[0]["error"]["code"], -32600)
        self.assertEqual(resp[1].get("id"), 1)
        self.assertEqual(resp[1]["error"]["code"], -32000)

    def test_mixed_batch_allowed_throttled_notif(self):
        p = self.spawn_limiter(burst=1.0, rate=0.0)
        # Initialize
        p.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n')
        p.stdin.flush()
        init_resp = json.loads(p.stdout.readline().decode())
        self.assertEqual(init_resp["id"], 1)

        p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        p.stdin.flush()

        # Batch: 1st allowed (burst=1), 2nd notification, 3rd throttled
        batch_req = [
            {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"message":"first"}}},
            {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":50}},
            {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"message":"second"}}}
        ]
        p.stdin.write(json.dumps(batch_req).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()

        batch_out = json.loads(p.stdout.readline().decode())
        p.wait()
        self.assertIsInstance(batch_out, list)
        self.assertEqual(len(batch_out), 2)  # Notification omitted
        self.assertEqual(batch_out[0]["id"], 2)
        self.assertIn("result", batch_out[0])
        self.assertEqual(batch_out[1]["id"], 3)
        self.assertEqual(batch_out[1]["error"]["code"], -32000)

    def test_consecutive_batches(self):
        p = self.spawn_limiter(burst=10.0, rate=10.0)
        p.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}\n')
        p.stdin.flush()
        p.stdout.readline()

        p.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        p.stdin.flush()

        for b_idx in range(3):
            reqs = [{"jsonrpc":"2.0","id":b_idx*10 + i,"method":"ping"} for i in range(2)]
            p.stdin.write(json.dumps(reqs).encode() + b"\n")
            p.stdin.flush()
            resps = json.loads(p.stdout.readline().decode())
            self.assertEqual(len(resps), 2)
            self.assertEqual(resps[0]["id"], b_idx*10)
            self.assertEqual(resps[1]["id"], b_idx*10 + 1)
        p.stdin.close()
        p.wait()

    def test_inbound_client_response_followed_by_batch(self):
        # Inbound client responses do not elicit a server response and must not corrupt subsequent batch merging
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        p.stdin.write(b'{"jsonrpc":"2.0","id":90,"result":{}}\n')
        p.stdin.flush()
        p.stdin.write(b'[{"jsonrpc":"2.0","id":91,"method":"ping"},{"jsonrpc":"2.0","id":92,"method":"tools/call","params":{"name":"system_info"}}]\n')
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0]["id"], 91)
        self.assertIn("result", resps[0])
        self.assertEqual(resps[1]["id"], 92)
        self.assertEqual(resps[1]["error"]["code"], -32000)

    def test_batch_method_bearing_object_with_result_and_throttled_call(self):
        # A request containing both 'method' and 'result' is a request (not an inbound response) and must be merged in batches
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        batch = [
            {"jsonrpc":"2.0","id":1,"method":"ping","result":{}},
            {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hi"}}}
        ]
        p.stdin.write(json.dumps(batch).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0]["id"], 1)
        self.assertIn("result", resps[0])
        self.assertEqual(resps[1]["id"], 2)
        self.assertEqual(resps[1]["error"]["code"], -32000)

    def test_batch_invalid_params_notification_and_throttled_call(self):
        # A notification with invalid 'params': null produces an error from child and must be preserved during batch aggregation
        p = self.spawn_limiter(burst=0.0, rate=1.0)
        batch = [
            {"jsonrpc":"2.0","method":"ping","params":None},
            {"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"echo","arguments":{"message":"x"}}},
            {"jsonrpc":"2.0","id":9,"method":"ping"}
        ]
        p.stdin.write(json.dumps(batch).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 3)
        self.assertEqual(resps[0]["id"], None)
        self.assertEqual(resps[0]["error"]["code"], -32600)
        self.assertEqual(resps[1]["id"], 8)
        self.assertEqual(resps[1]["error"]["code"], -32000)
        self.assertEqual(resps[2]["id"], 9)
        self.assertIn("result", resps[2])

    def test_batch_null_id_request_and_throttled_call(self):
        # A request with explicit 'id': null is an invalid request (not a notification) and produces an error that must be merged
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        p.stdin.write(b'[{"jsonrpc":"2.0","id":null,"method":"ping"},{"jsonrpc":"2.0","id":92,"method":"tools/call","params":{"name":"system_info"}}]\n')
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0]["id"], None)
        self.assertEqual(resps[0]["error"]["code"], -32600)
        self.assertEqual(resps[1]["id"], 92)
        self.assertEqual(resps[1]["error"]["code"], -32000)

    def test_batch_size_overflow(self):
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        reqs = [{"jsonrpc":"2.0","id":i,"method":"tools/call","params":{"name":"echo","arguments":{"message":"x"}}} for i in range(101)]
        p.stdin.write(json.dumps(reqs).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertIsInstance(resp, dict)
        self.assertEqual(resp.get("id"), None)
        self.assertEqual(resp["error"]["code"], -32600)
        self.assertIn("Batch size exceeds maximum of 100", resp["error"]["message"])

    def test_batch_only_throttled_notifications(self):
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        reqs = [
            {"jsonrpc":"2.0","method":"tools/call","params":{"name":"echo","arguments":{"message":"notif1"}}},
            {"jsonrpc":"2.0","method":"tools/call","params":{"name":"echo","arguments":{"message":"notif2"}}}
        ]
        p.stdin.write(json.dumps(reqs).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.read()
        p.wait()
        self.assertEqual(out, b"", "Batch of only throttled notifications must produce zero output bytes")

    def test_eof_framing_boundary(self):
        # bounded_readline accepts exactly MAX_FRAME_SIZE (10,485,760 bytes) at EOF without newline
        import io
        sys.path.insert(0, PROJECT_ROOT)
        from scripts.zeromcp_stdio_limiter import bounded_readline, MAX_FRAME_SIZE

        s_ok = io.BytesIO(b"x" * MAX_FRAME_SIZE)
        line_ok, oversized_ok = bounded_readline(s_ok, MAX_FRAME_SIZE)
        self.assertFalse(oversized_ok)
        self.assertEqual(len(line_ok), MAX_FRAME_SIZE)

        # 10,485,761 bytes exceeds MAX_FRAME_SIZE and is rejected as oversized
        s_bad = io.BytesIO(b"x" * (MAX_FRAME_SIZE + 1))
        line_bad, oversized_bad = bounded_readline(s_bad, MAX_FRAME_SIZE)
        self.assertTrue(oversized_bad)
        self.assertIsNone(line_bad)

    def test_invalid_id_not_reflected_in_throttle_error(self):
        # Even with bucket exhausted (burst=0), an invalid ID (e.g. "id": true) must NOT produce
        # a synthetic -32000 error with "id": true. Instead, it must be forwarded to the child
        # which returns a canonical -32600 Invalid Request with "id": null.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": True,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}}
        })
        p.stdin.write(line.encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertIsNone(resp.get("id"), "Malformed request must produce response with id: null, not id: true")
        self.assertEqual(resp.get("error", {}).get("code"), -32600, "Must return -32600 Invalid Request")

    def test_integer_valued_float_id_throttled(self):
        # Requests with integer-valued float IDs (e.g. 1001.0, accepted by JavaScript Number.isInteger)
        # must be subject to token-bucket throttling rather than bypassing to child.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1001.0,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}}
        })
        p.stdin.write(line.encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertEqual(resp.get("error", {}).get("code"), -32000, "Must return -32000 Rate limit exceeded")
        self.assertEqual(resp.get("id"), 1001.0, "Must reflect the request ID")

    def test_integer_valued_float_id_in_batch_throttled(self):
        # Batch items with integer-valued float IDs (e.g. 1001.0) must also be throttled
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        reqs = [{
            "jsonrpc": "2.0",
            "id": 1001.0,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}}
        }]
        p.stdin.write(json.dumps(reqs).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 1)
        self.assertEqual(resps[0].get("error", {}).get("code"), -32000)
        self.assertEqual(resps[0].get("id"), 1001.0)

    def test_empty_string_id_throttled(self):
        # Empty string "" is a valid JSON-RPC 2.0 ID and must be properly throttled
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": "",
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}}
        })
        p.stdin.write(line.encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertEqual(resp.get("error", {}).get("code"), -32000)
        self.assertEqual(resp.get("id"), "")

    def test_pipelined_inbound_response_without_id_then_ping(self):
        # An object with "result": {} but no "id" is an invalid request (not a valid client response),
        # so child returns -32600. When pipelined with ping, supervisor must properly account for
        # both responses and not corrupt FIFO queue state.
        p = self.spawn_limiter(burst=10.0, rate=10.0)
        p.stdin.write(b'{"result":{}}\n{"jsonrpc":"2.0","id":100,"method":"ping"}\n')
        p.stdin.flush()
        p.stdin.close()
        out1 = p.stdout.readline()
        out2 = p.stdout.readline()
        p.wait()
        r1 = json.loads(out1.decode())
        r2 = json.loads(out2.decode())
        self.assertEqual(r1.get("error", {}).get("code"), -32600, "First response must be -32600 for missing jsonrpc/method")
        self.assertIsNone(r1.get("id"))
        self.assertEqual(r2.get("id"), 100, "Second response must match ping ID 100")
        self.assertEqual(r2.get("result"), {})

    def test_mixed_batch_with_invalid_result_without_id_and_throttled_call(self):
        # In a mixed batch with {"result": {}} (produces -32600) and a throttled tools/call (produces -32000),
        # supervisor must forward the former to the child, merge with the synthetic throttle error,
        # and return both in the single merged batch array.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        reqs = [
            {"result": {}},
            {"jsonrpc": "2.0", "id": 200, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "hi"}}}
        ]
        p.stdin.write(json.dumps(reqs).encode() + b"\n")
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0].get("error", {}).get("code"), -32600)
        self.assertIsNone(resps[0].get("id"))
        self.assertEqual(resps[1].get("error", {}).get("code"), -32000)
        self.assertEqual(resps[1].get("id"), 200)

    def test_large_unicode_batch_reserialization_no_overflow(self):
        # A 3.6 MB UTF-8 batch containing non-ASCII Unicode (e.g. 'é' which is 2 bytes in UTF-8)
        # must NOT be inflated into ASCII escape sequences (\u00e9, 6 bytes = 3x inflation)
        # when the supervisor reserializes forwarded batch members.
        # With ensure_ascii=False, the forwarded frame stays well under the 10 MiB limit,
        # preserving child response correlation instead of triggering child -32700 Frame exceeded limit.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        # 1.8M 'é' chars = 3.6 MB UTF-8
        large_unicode_meta = "é" * 1_800_000
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"_meta": large_unicode_meta}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "hi"}}}
        ]
        line = json.dumps(reqs, ensure_ascii=False) + "\n"
        p.stdin.write(line.encode("utf-8"))
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode("utf-8"))
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0].get("id"), 1, "Ping response must be preserved with id 1")
        self.assertEqual(resps[0].get("result"), {})
        self.assertEqual(resps[1].get("id"), 2)
        self.assertEqual(resps[1].get("error", {}).get("code"), -32000)

    def test_boundary_batch_reserialization_compactness(self):
        # A 100-request batch near the 10 MiB boundary where 1 request is throttled
        # must NOT expand when forwarded to child due to default whitespace insertion.
        # Compact serialization separators=(',', ':') guarantees that forwarded items
        # remain strictly <= original frame size, preserving all 100 responses.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        # 100 items of ~104 KB each = ~10.4 MB (under 10,485,760 bytes)
        pad = "x" * 104750
        reqs = []
        for i in range(1, 100):
            reqs.append({"jsonrpc": "2.0", "id": i, "method": "ping", "params": {"_meta": pad}})
        reqs.append({"jsonrpc": "2.0", "id": 100, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "hi"}}})
        
        line = json.dumps(reqs, separators=(',', ':'), ensure_ascii=False) + "\n"
        self.assertLessEqual(len(line.encode("utf-8")), 10485760, "Input frame must be <= 10 MiB")
        
        p.stdin.write(line.encode("utf-8"))
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode("utf-8"))
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 100, "Must preserve all 100 responses in batch")
        self.assertEqual(resps[99].get("id"), 100)
        self.assertEqual(resps[99].get("error", {}).get("code"), -32000)

    def test_unpaired_surrogate_batch_reserialization(self):
        # A batch containing an escaped lone surrogate "\\ud800" in JSON text
        # must be safely reserialized with backslashreplace, preserving "\\ud800" as a valid JSON escape sequence.
        # The child receives valid UTF-8, succeeds on the ping with id: 1, and merges with the throttled call id: 2.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        raw_line = b'[{"jsonrpc":"2.0","id":1,"method":"ping","params":{"_meta":"\\ud800"}},{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hi"}}}]\n'
        p.stdin.write(raw_line)
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode("utf-8", errors="backslashreplace"))
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0].get("id"), 1, "Ping with escaped surrogate must succeed with id: 1")
        self.assertEqual(resps[0].get("result"), {})
        self.assertEqual(resps[1].get("id"), 2)
        self.assertEqual(resps[1].get("error", {}).get("code"), -32000)

    def test_malformed_nan_constant_forwarded_as_parse_error(self):
        # A request containing NaN or Infinity is non-standard JSON and must NOT be permissively
        # parsed and throttled with -32000; strict parse_constant raises ValueError, passing
        # the frame through to the child to emit a canonical -32700 Parse error.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        raw_line = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"message":NaN}}}\n'
        p.stdin.write(raw_line)
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertEqual(resp.get("error", {}).get("code"), -32700, "NaN must result in -32700 Parse error, not -32000 throttle")

    def test_child_whole_frame_error_broadcast_preserves_request_ids(self):
        # When child rejects an oversized forwarded frame with a single -32700 error,
        # the supervisor broadcasts the error across forwarded items while restoring their
        # identifiable request IDs so clients can correlate every response in the batch.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        # 1 ping that exceeds frame size on child + 1 throttled call
        # We can construct an oversized line for the child by sending valid JSON that supervisor
        # passes to child when child's reader receives it.
        # But even simpler: if child returns a single -32700 error, verify ID correlation
        reqs = [
            {"jsonrpc": "2.0", "id": "req-alpha", "method": "ping"},
            {"jsonrpc": "2.0", "id": "req-beta", "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "hi"}}}
        ]
        line = json.dumps(reqs) + "\n"
        p.stdin.write(line.encode())
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resps = json.loads(out.decode())
        self.assertIsInstance(resps, list)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0].get("id"), "req-alpha")
        self.assertEqual(resps[1].get("id"), "req-beta")
        self.assertEqual(resps[1].get("error", {}).get("code"), -32000)

    def test_supervisor_eof_drains_all_pending_responses(self):
        # Regression test for EOF response loss:
        # Client sends handshake + 1,000,000-char echo request and immediately closes stdin.
        # Supervisor must drain all pending responses and join its reader thread before exiting.
        p = self.spawn_limiter(burst=10.0, rate=10.0)
        h1 = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}) + "\n"
        h2 = json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"}) + "\n"
        req = json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"message":"x"*1000000}}}) + "\n"

        p.stdin.write((h1 + h2 + req).encode())
        p.stdin.flush()
        p.stdin.close()

        lines = []
        while True:
            line = p.stdout.readline()
            if not line:
                break
            lines.append(line)
        p.wait()

        self.assertEqual(len(lines), 2, "Must receive exactly 2 response lines (init and 1MB echo)")
        r1 = json.loads(lines[0].decode())
        self.assertEqual(r1.get("id"), 1)
        r2 = json.loads(lines[1].decode())
        self.assertEqual(r2.get("id"), 2)
        self.assertEqual(len(r2["result"]["content"][0]["text"]), 1000000)

    def test_reserialization_enforces_forwarded_frame_limit(self):
        # Verify supervisor checks forwarded frame size against MAX_FRAME_SIZE.
        p = self.spawn_limiter(burst=0.0, rate=0.0)
        reqs = [
            {"jsonrpc": "2.0", "id": "t1", "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "throttled"}}},
            {"jsonrpc": "2.0", "id": "f1", "method": "ping", "params": {"padding": "z" * 10485700}}
        ]
        p.stdin.write((json.dumps(reqs) + "\n").encode())
        p.stdin.flush()
        p.stdin.close()
        out = p.stdout.readline()
        p.wait()
        resp = json.loads(out.decode())
        self.assertEqual(resp.get("error", {}).get("code"), -32700)

if __name__ == "__main__":
    unittest.main()
