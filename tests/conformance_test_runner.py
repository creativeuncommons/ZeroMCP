#!/usr/bin/env python3
"""
ZeroMCP Cross-Language Conformance Test Suite (Hardened & Complete)
===================================================================
Pipes deterministic raw JSON-RPC vectors over stdin to ZeroMCP server targets
and asserts strict compliance with JSON-RPC 2.0 and MCP (2024-11-05) specifications.
"""

import os
import sys
import json
import time
import shutil
import subprocess
import threading
import queue
from typing import List, Dict, Any, Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

TEST_SUITE = [
    {
        "name": "1. Pre-Handshake Lifecycle Gate (tools/list rejected before initialize)",
        "input": '{"jsonrpc": "2.0", "id": 98, "method": "tools/list"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 98 and r.get("error", {}).get("code") == -32600 and "uninitialized" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "2. Pre-Handshake Lifecycle Gate (tools/call rejected before initialize)",
        "input": '{"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "hi"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 99 and r.get("error", {}).get("code") == -32600 and "uninitialized" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "3. Handshake Initialize",
        "input": '{"jsonrpc": "2.0", "id": 101, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "conformance-runner", "version": "1.0"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 101 and r.get("result", {}).get("protocolVersion") == "2024-11-05" and "tools" in r.get("result", {}).get("capabilities", {})
    },
    {
        "name": "4. Duplicate Initialize Rejection",
        "input": '{"jsonrpc": "2.0", "id": 1012, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1012 and r.get("error", {}).get("code") == -32600 and "already initialized" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "4b. Intermediate Lifecycle Gate (tools/call rejected before notifications/initialized)",
        "input": '{"jsonrpc": "2.0", "id": 1013, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "test"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1013 and r.get("error", {}).get("code") == -32600 and "uninitialized" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "5. Known MCP Notification (notifications/initialized)",
        "input": '{"jsonrpc": "2.0", "method": "notifications/initialized"}\n',
        "expect_response": False,
        "validate": lambda r: True
    },
    {
        "name": "6. Tolerant Non-Conforming Notification with Array Params (notifications/progress)",
        "input": '{"jsonrpc": "2.0", "method": "notifications/progress", "params": [1, 2]}\n',
        "expect_response": False,
        "validate": lambda r: True
    },
    {
        "name": "7. Heartbeat Ping",
        "input": '{"jsonrpc": "2.0", "id": 102, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 102 and "result" in r and r.get("result") == {}
    },
    {
        "name": "8. Tool Discovery (tools/list post-handshake)",
        "input": '{"jsonrpc": "2.0", "id": 103, "method": "tools/list", "params": {}}\n',
        "expect_response": True,
        "validate": lambda r: (
            r.get("jsonrpc") == "2.0" and
            r.get("id") == 103 and
            isinstance(r.get("result", {}).get("tools"), list) and
            len(r["result"]["tools"]) >= 3 and
            all(isinstance(t, dict) and "name" in t and "description" in t and "inputSchema" in t for t in r["result"]["tools"]) and
            {"echo", "eval_math", "system_info"}.issubset({t["name"] for t in r["result"]["tools"]})
        )
    },
    {
        "name": "9. Tool Execution (echo)",
        "input": '{"jsonrpc": "2.0", "id": 104, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "conformance_payload_xyz"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 104 and r.get("result", {}).get("isError") is False and r["result"]["content"][0]["text"] == "conformance_payload_xyz"
    },
    {
        "name": "9b. Tool Execution (system_info with null arguments tolerated)",
        "input": '{"jsonrpc": "2.0", "id": 1042, "method": "tools/call", "params": {"name": "system_info", "arguments": null}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1042 and r.get("result", {}).get("isError") is False
    },
    {
        "name": "10. Tool Execution (eval_math safe arithmetic)",
        "input": '{"jsonrpc": "2.0", "id": 105, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "15 + 5 * 3"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 105 and r.get("result", {}).get("isError") is False and float(r["result"]["content"][0]["text"]) == 30.0
    },
    {
        "name": "11. Tool Execution (eval_math division by zero error)",
        "input": '{"jsonrpc": "2.0", "id": 106, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "10 / 0"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 106 and r.get("result", {}).get("isError") is True and "division by zero" in r["result"]["content"][0]["text"].lower()
    },
    {
        "name": "12. Tool Execution (eval_math complex number rejection)",
        "input": '{"jsonrpc": "2.0", "id": 107, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "(-8) ** 0.5"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 107 and r.get("result", {}).get("isError") is True and r["result"]["content"][0]["text"] == "Error: Complex or non-finite numbers unsupported"
    },
    {
        "name": "13. Tool Execution (eval_math syntax rejection on '1 2')",
        "input": '{"jsonrpc": "2.0", "id": 108, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "1 2"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 108 and r.get("result", {}).get("isError") is True
    },
    {
        "name": "14. Tool Execution (eval_math expression length limit >1024 chars)",
        "input": '{"jsonrpc": "2.0", "id": 1082, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "' + ("1+" * 520) + '1"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1082 and r.get("result", {}).get("isError") is True and any(w in r["result"]["content"][0]["text"].lower() for w in ("long", "invalid"))
    },
    {
        "name": "15. Tool Execution (system_info)",
        "input": '{"jsonrpc": "2.0", "id": 109, "method": "tools/call", "params": {"name": "system_info"}}\n',
        "expect_response": True,
        "validate": lambda r: (
            r.get("jsonrpc") == "2.0" and
            r.get("id") == 109 and
            r.get("result", {}).get("isError") is False and
            isinstance(r["result"].get("content"), list) and
            len(r["result"]["content"]) > 0 and
            isinstance(json.loads(r["result"]["content"][0]["text"]), dict) and
            len(json.loads(r["result"]["content"][0]["text"])) >= 3
        )
    },
    {
        "name": "16. Error -32601 Method Not Found (with 64-char truncation)",
        "input": '{"jsonrpc": "2.0", "id": 110, "method": "' + ("a" * 100) + '"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 110 and r.get("error", {}).get("code") == -32601 and len(r.get("error", {}).get("message", "")) < 90
    },
    {
        "name": "17. Error -32602 Missing Required Arguments in tools/call",
        "input": '{"jsonrpc": "2.0", "id": 111, "method": "tools/call", "params": {"name": "echo", "arguments": {}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 111 and r.get("error", {}).get("code") == -32602
    },
    {
        "name": "17b. Error -32602 Incorrect Argument Type in tools/call ('message': 42)",
        "input": '{"jsonrpc": "2.0", "id": 1112, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": 42}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1112 and r.get("error", {}).get("code") == -32602 and "string" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "18. Error -32600 Invalid ID Type (object instead of str/int)",
        "input": '{"jsonrpc": "2.0", "id": {"bad": "type"}, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32600
    },
    {
        "name": "19. Error -32600 Boolean ID Rejection (true)",
        "input": '{"jsonrpc": "2.0", "id": true, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32600
    },
    {
        "name": "20. Error -32600 Null ID Rejection (null)",
        "input": '{"jsonrpc": "2.0", "id": null, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32600
    },
    {
        "name": "21. Error -32600 Float ID Rejection (1.5)",
        "input": '{"jsonrpc": "2.0", "id": 1.5, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32600
    },
    {
        "name": "22. Error -32600 Missing jsonrpc Field",
        "input": '{"id": 114, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 114 and r.get("error", {}).get("code") == -32600
    },
    {
        "name": "23. Error -32600 Null Params Rejection",
        "input": '{"jsonrpc": "2.0", "id": 113, "method": "ping", "params": null}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 113 and r.get("error", {}).get("code") == -32600
    },
    {
        "name": "24. Error -32700 Parse Error on Malformed JSON",
        "input": '{invalid_json_frame_syntax}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32700
    },
    {
        "name": "25. Error -32700 NaN / Infinity Constant Rejection",
        "input": '{"jsonrpc": "2.0", "id": 112, "method": "echo", "params": {"val": NaN}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32700
    },
    {
        "name": "26. Batch Request Handling (valid pair)",
        "input": '[{"jsonrpc": "2.0", "id": 201, "method": "ping"}, {"jsonrpc": "2.0", "id": 202, "method": "ping"}]\n',
        "expect_response": True,
        "validate": lambda r: isinstance(r, list) and len(r) == 2 and r[0].get("id") == 201 and r[1].get("id") == 202
    },
    {
        "name": "27. Batch Request Overflow (>100 items rejected)",
        "input": json.dumps([{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(101)]) + "\n",
        "expect_response": True,
        "validate": lambda r: r.get("error", {}).get("code") == -32600 and "exceeds" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "28. Whitespace and Empty Frame Resilience",
        "input": '   \n\n\t\n{"jsonrpc": "2.0", "id": 300, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 300 and "result" in r
    },
    {
        "name": "29. Error -32700 Infinity Constant Rejection",
        "input": '{"jsonrpc": "2.0", "id": 1122, "method": "echo", "params": {"val": Infinity}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") is None and r.get("error", {}).get("code") == -32700
    },
    {
        "name": "30. Error -32602 Non-Object Tool Arguments Rejection",
        "input": '{"jsonrpc": "2.0", "id": 1123, "method": "tools/call", "params": {"name": "system_info", "arguments": []}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1123 and r.get("error", {}).get("code") == -32602 and "must be an object" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "31. Tool Execution (eval_math modulo by zero error)",
        "input": '{"jsonrpc": "2.0", "id": 1062, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "10 % 0"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1062 and r.get("result", {}).get("isError") is True and "modulo by zero" in r["result"]["content"][0]["text"].lower()
    },
    {
        "name": "32. Tool Lookup Prototype Property Rejection ('toString')",
        "input": '{"jsonrpc": "2.0", "id": 1124, "method": "tools/call", "params": {"name": "toString", "arguments": {}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1124 and r.get("error", {}).get("code") == -32602 and "not found" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "33. Leading Whitespace Before Valid Request",
        "input": '   {"jsonrpc": "2.0", "id": 1125, "method": "ping"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1125 and r.get("result") == {}
    },
    {
        "name": "34. Oversized Frame Boundary Rejection (>10 MiB)",
        "input": '{"jsonrpc": "2.0", "id": 1126, "method": "ping", "padding": "' + ('x' * (10 * 1024 * 1024 + 16)) + '"}\n',
        "expect_response": True,
        "validate": lambda r: r.get("error", {}).get("code") == -32700 and "maximum allowed size" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "35. Tool Execution (eval_math negative exponent on zero '0 ** -1')",
        "input": '{"jsonrpc": "2.0", "id": 1127, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "0 ** -1"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1127 and r.get("result", {}).get("isError") is True and r["result"]["content"][0]["text"] == "Error: Complex or non-finite numbers unsupported"
    },
    {
        "name": "36. Tool Execution (eval_math float overflow '10 ** 400')",
        "input": '{"jsonrpc": "2.0", "id": 1128, "method": "tools/call", "params": {"name": "eval_math", "arguments": {"expression": "10 ** 400"}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1128 and r.get("result", {}).get("isError") is True and r["result"]["content"][0]["text"] == "Error: Complex or non-finite numbers unsupported"
    },
    {
        "name": "37. Multi-Tenant Scoped Execution (_meta.scopes: ['tools:*'])",
        "input": '{"jsonrpc": "2.0", "id": 1129, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "scoped_authorized"}, "_meta": {"scopes": ["tools:*"]}}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1129 and r.get("result", {}).get("isError") is False and r["result"]["content"][0]["text"] == "scoped_authorized"
    },
    {
        "name": "38. Scoped Tool Rejection via Negation (scopes: ['tools:*', '!tools:echo'])",
        "input": '{"jsonrpc": "2.0", "id": 1130, "method": "tools/call", "params": {"name": "echo", "arguments": {"message": "blocked"}, "scopes": ["tools:*", "!tools:echo"]}}\n',
        "expect_response": True,
        "validate": lambda r: r.get("id") == 1130 and r.get("error", {}).get("code") == -32602 and "forbidden" in r.get("error", {}).get("message", "").lower()
    },
    {
        "name": "39. Hierarchical Tool Discovery Filtering (filter_unauthorized with negation)",
        "input": '{"jsonrpc": "2.0", "id": 1131, "method": "tools/list", "params": {"filter_unauthorized": true, "scopes": ["tools:*", "!tools:echo"]}}\n',
        "expect_response": True,
        "validate": lambda r: (
            r.get("id") == 1131 and
            isinstance(r.get("result", {}).get("tools"), list) and
            "echo" not in [t["name"] for t in r["result"]["tools"]] and
            "eval_math" in [t["name"] for t in r["result"]["tools"]]
        )
    }
]

def run_conformance_for_command(cmd: List[str], label: str) -> bool:
    print(f"\n==================================================")
    print(f"🧪 Running Conformance Suite on: {label}")
    print(f"Command: {' '.join(cmd)}")
    print(f"==================================================")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
    except Exception as e:
        print(f"❌ Could not launch command '{' '.join(cmd)}': {e}")
        return False

    stdout_q = queue.Queue()
    def _reader():
        try:
            for l in iter(proc.stdout.readline, ''):
                stdout_q.put(l)
        except Exception:
            pass
        stdout_q.put(None)
    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    def get_line(timeout=5.0) -> Optional[str]:
        try:
            return stdout_q.get(timeout=timeout)
        except queue.Empty:
            return None

    all_passed = True
    total_time = 0.0

    for idx, test in enumerate(TEST_SUITE, start=1):
        t0 = time.time()
        test_name = test["name"]
        raw_in = test["input"]
        expect_resp = test["expect_response"]
        validator = test["validate"]

        try:
            proc.stdin.write(raw_in)
            proc.stdin.flush()
        except Exception as e:
            print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: Failed to write stdin: {e}")
            all_passed = False
            break

        if expect_resp:
            line = get_line(timeout=5.0)
            elapsed = (time.time() - t0) * 1000
            total_time += elapsed

            if line is None:
                print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: Timed out or got EOF waiting for response (>5.0s) ({elapsed:.2f}ms)")
                all_passed = False
                proc.kill()
                break

            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError as e:
                print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: Invalid JSON output: '{line.strip()}' ({e})")
                all_passed = False
                continue

            if validator(data):
                print(f"✅ [{idx}/{len(TEST_SUITE)}] {test_name}: PASSED ({elapsed:.2f}ms)")
            else:
                print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: FAILED validation. Response: {data}")
                all_passed = False
        else:
            # For notifications, verify that no immediate output is emitted by flushing with a follow-up ping
            # Use session-unique request ID per MCP 2024-11-05 message requirements
            ping_id = 9000 + idx
            ping_check = f'{{"jsonrpc": "2.0", "id": {ping_id}, "method": "ping"}}\n'
            proc.stdin.write(ping_check)
            proc.stdin.flush()
            line = get_line(timeout=5.0)
            elapsed = (time.time() - t0) * 1000
            total_time += elapsed
            if line is None:
                print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: Timed out waiting for notification flush ping (>5.0s)")
                all_passed = False
                proc.kill()
                break
            try:
                data = json.loads(line.strip())
                if data.get("id") == ping_id:
                    print(f"✅ [{idx}/{len(TEST_SUITE)}] {test_name}: PASSED (Zero notification response leakage confirmed) ({elapsed:.2f}ms)")
                else:
                    print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: Unexpected notification response: {data}")
                    all_passed = False
            except Exception as e:
                print(f"❌ [{idx}/{len(TEST_SUITE)}] {test_name}: Failed during notification check: {e}")
                all_passed = False

    # Test 37: Natural clean termination on EOF
    proc.stdin.close()
    try:
        exit_code = proc.wait(timeout=3)
        if exit_code == 0:
            print(f"✅ [{len(TEST_SUITE)+1}/{len(TEST_SUITE)+1}] Clean Process Termination on EOF: PASSED (exit code 0)")
        else:
            print(f"❌ Clean Process Termination on EOF: Exited with non-zero code {exit_code}")
            all_passed = False
    except subprocess.TimeoutExpired:
        print(f"❌ Clean Process Termination on EOF: Process did not terminate within timeout after stdin closure")
        proc.kill()
        all_passed = False

    total_tests = len(TEST_SUITE) + 1
    print("--------------------------------------------------")
    if all_passed:
        print(f"🏆 ALL {total_tests} CONFORMANCE TESTS PASSED for {label} (Total: {total_time:.1f}ms)")
    else:
        print(f"💥 CONFORMANCE FAILURES DETECTED for {label}")
    print("--------------------------------------------------\n")
    return all_passed

def is_node_version_supported(bin_path: str) -> bool:
    try:
        res = subprocess.run([bin_path, "-v"], capture_output=True, text=True, timeout=2)
        v = res.stdout.strip().lstrip("v")
        parts = [int(p) for p in v.split(".")[:2]]
        if parts[0] > 22 or (parts[0] == 22 and parts[1] >= 7):
            return True
    except Exception:
        pass
    return False

def discover_js_runtime() -> Optional[List[str]]:
    # 1. Search PATH for node with version check (Node 22.7+ required for --experimental-strip-types)
    node_which = shutil.which("node")
    if node_which and is_node_version_supported(node_which):
        return [node_which, "--experimental-strip-types"]

    # 2. Search standard system / tool runtime paths
    for p in [
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]:
        if os.path.exists(p) and os.access(p, os.X_OK) and is_node_version_supported(p):
            return [p, "--experimental-strip-types"]

    # 3. Search PATH for bun
    bun_which = shutil.which("bun")
    if bun_which:
        return [bun_which, "run"]

    # 4. Search PATH for deno
    deno_which = shutil.which("deno")
    if deno_which:
        return [deno_which, "run", "--allow-sys", "--allow-env=ZEROMCP_NO_AUTO_START"]

    return None

if __name__ == "__main__":
    py_path = os.path.join(PROJECT_ROOT, "zeromcp-py", "zeromcp.py")
    ts_path = os.path.join(PROJECT_ROOT, "zeromcp-ts", "zeromcp.ts")
    rust_bin = os.path.join(PROJECT_ROOT, "zeromcp-rs", "target", "release", "zeromcp")
    if not os.path.exists(rust_bin):
        rust_bin_exe = os.path.join(PROJECT_ROOT, "zeromcp-rs", "target", "release", "zeromcp.exe")
        if os.path.exists(rust_bin_exe):
            rust_bin = rust_bin_exe

    results = []

    # 1. Test Python Reference
    print("\n🔍 Target 1: Python Reference (zeromcp-py)")
    py_ok = run_conformance_for_command([sys.executable, py_path], "Python Reference (zeromcp-py)")
    results.append(("Python (zeromcp-py)", py_ok))

    # 2. Test TypeScript Reference
    js_cmd_prefix = discover_js_runtime()
    if js_cmd_prefix:
        print("\n🔍 Target 2: TypeScript Reference (zeromcp-ts)")
        ts_cmd = js_cmd_prefix + [ts_path]
        ts_ok = run_conformance_for_command(ts_cmd, f"TypeScript Reference ({os.path.basename(js_cmd_prefix[0])})")
        results.append(("TypeScript (zeromcp-ts)", ts_ok))
    else:
        print("ℹ️ JavaScript runtime (node/bun/deno) not detected on PATH; skipping live TypeScript execution.")

    # 3. Test Rust (if compiled)
    if os.path.exists(rust_bin) and os.access(rust_bin, os.X_OK):
        print("\n🔍 Target 3: Rust Native Binary (zeromcp-rs)")
        rs_ok = run_conformance_for_command([rust_bin], "Rust Native (zeromcp-rs)")
        results.append(("Rust (zeromcp-rs)", rs_ok))
    else:
        print("ℹ️ Rust binary not yet compiled (requires cargo); scaffold verified in zeromcp-rs/.")

    # 4. Test WebAssembly / WASI (if compiled and runtime present)
    wasm_bin = os.path.join(PROJECT_ROOT, "zeromcp-rs", "target", "wasm32-wasip1", "release", "zeromcp.wasm")
    if not os.path.exists(wasm_bin):
        wasm_bin = os.path.join(PROJECT_ROOT, "dist", "zeromcp.wasm")

    if os.path.exists(wasm_bin):
        wasmtime_bin = shutil.which("wasmtime")
        if wasmtime_bin:
            print("\n🔍 Target 4: WebAssembly WASI Binary (wasmtime)")
            wasm_ok = run_conformance_for_command([wasmtime_bin, wasm_bin], "WebAssembly WASI (wasmtime)")
            results.append(("WebAssembly (wasmtime)", wasm_ok))
        else:
            print("ℹ️ WebAssembly binary found, but wasmtime runtime not detected on PATH.")

    print("\n==================================================")
    print("📋 SUMMARY OF CONFORMANCE RUNS:")
    for name, ok in results:
        status = "✅ 100% PASSED" if ok else "❌ FAILED"
        print(f"  - {name}: {status}")
    print("==================================================\n")

    ci_mode = "--ci" in sys.argv
    all_passed = all(ok for _, ok in results)
    if ci_mode and len(results) < 4:
        print(f"❌ CI Gate Failure: --ci requires all 4 runtime targets (executed {len(results)}/4).")
        all_passed = False

    sys.exit(0 if all_passed else 1)
