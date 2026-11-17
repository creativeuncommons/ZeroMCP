#!/usr/bin/env python3
"""
ZeroMCP Stdio Rate-Limiting Supervisor
=====================================
Wraps any Model Context Protocol stdio server with strict token-bucket rate limiting
on individual `tools/call` invocations, including calls embedded within batch requests.
Operates using pure Python standard library over bidirectional standard I/O pipes.

Compatible with Python 3.8+.

Preserves all JSON-RPC 2.0 wire invariants:
- Notification silence: Throttled notifications without an 'id' emit zero response bytes.
- Batch aggregation: Batch requests always return a single JSON array response.
- Concurrency safety: Standard output writes are strictly serialized via a shared lock.
- Bounded ingestion: Inbound frames exceeding 10 MiB are rejected with -32700.

Usage:
    python3 zeromcp_stdio_limiter.py --rate 30 --burst 10 -- python3 zeromcp-py/zeromcp.py
"""

import sys
import os
import json
import time
import math
import subprocess
import threading
import queue
from typing import List, Dict, Any, Optional, Tuple

MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10 MiB
MAX_BATCH = 100

class TokenBucketLimiter:
    def __init__(self, rate_per_sec: float, burst_capacity: float):
        self.rate = rate_per_sec
        self.capacity = burst_capacity
        self.tokens = burst_capacity
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False

def bounded_readline(stream, max_bytes: int = MAX_FRAME_SIZE) -> Tuple[Optional[bytes], bool]:
    """Reads a line up to max_bytes. Returns (line_bytes, is_oversized)."""
    line = stream.readline(max_bytes + 1)
    if not line:
        return None, False
    if len(line) > max_bytes:
        # Drain remainder of oversized line
        while not line.endswith(b"\n"):
            chunk = stream.readline(65536)
            if not chunk:
                break
            line = chunk
        return None, True
    return line, False

def is_valid_notification(item: Any) -> bool:
    """Returns True iff item is a well-formed JSON-RPC 2.0 notification."""
    if not (
        isinstance(item, dict)
        and item.get("jsonrpc") == "2.0"
        and isinstance(item.get("method"), str)
        and "id" not in item
    ):
        return False
    # If params is present, it MUST be structured (dict or list); params: null is invalid
    if "params" in item and not isinstance(item["params"], (dict, list)):
        return False
    return True

def is_valid_request(item: Any) -> bool:
    """Returns True iff item has a valid JSON-RPC 2.0 envelope and a valid string or integer ID (including integer-valued floats)."""
    if not isinstance(item, dict):
        return False
    if item.get("jsonrpc") != "2.0":
        return False
    if not isinstance(item.get("method"), str):
        return False
    if "id" not in item:
        return False
    req_id = item["id"]
    if isinstance(req_id, bool):
        return False
    if not isinstance(req_id, (str, int)):
        if isinstance(req_id, float) and math.isfinite(req_id) and req_id.is_integer():
            pass
        else:
            return False
    if "params" in item and not isinstance(item["params"], (dict, list)):
        return False
    return True

def produces_server_response(item: Any) -> bool:
    """Returns True iff item causes a JSON-RPC server to emit a response line/object."""
    if not isinstance(item, dict):
        # Non-dict primitives (numbers, strings) trigger -32600 Invalid Request
        return True
    # Inbound client responses MUST contain 'id', ('result' or 'error'), and lack 'method'
    if "id" in item and ("result" in item or "error" in item) and "method" not in item:
        return False
    # Valid notifications ('method' present, structured params, and 'id' omitted) never elicit a response
    if is_valid_notification(item):
        return False
    # All other dicts (requests, or invalid notifications/objects) produce a response
    return True

def stdout_reader(proc: subprocess.Popen, pending_q: queue.Queue, stdout_lock: threading.Lock):
    try:
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                task = pending_q.get_nowait()
            except queue.Empty:
                task = ('PASSTHROUGH',)

            action = task[0]
            if action == 'PASSTHROUGH':
                with stdout_lock:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
            elif action == 'BATCH_MERGE':
                synth_map = task[1]
                expected_forward = task[2]  # List of (orig_idx, req_item)
                try:
                    child_resps = json.loads(line.decode("utf-8", errors="surrogatepass"))
                    if not isinstance(child_resps, list):
                        child_resps = [child_resps]
                except Exception:
                    child_resps = []

                # Correlate child responses to original indices
                unmatched_resps = list(child_resps)
                used_resp_indices = set()

                # Phase 1: Match by ID where available
                for orig_idx, req_item in expected_forward:
                    if isinstance(req_item, dict) and "id" in req_item and req_item["id"] is not None:
                        req_id = req_item["id"]
                        for r_idx, r in enumerate(unmatched_resps):
                            if r_idx not in used_resp_indices and isinstance(r, dict) and r.get("id") == req_id:
                                synth_map[orig_idx] = r
                                used_resp_indices.add(r_idx)
                                break

                # Phase 2: Positional fallback or whole-frame error broadcast
                if len(child_resps) == 1 and isinstance(child_resps[0], dict) and child_resps[0].get("error", {}).get("code") == -32700:
                    frame_err = child_resps[0].get("error", {})
                    for orig_idx, req_item in expected_forward:
                        if orig_idx not in synth_map:
                            req_id = req_item.get("id") if isinstance(req_item, dict) else None
                            synth_map[orig_idx] = {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": frame_err
                            }
                else:
                    remaining_resps = [r for r_idx, r in enumerate(unmatched_resps) if r_idx not in used_resp_indices]
                    rem_idx = 0
                    for orig_idx, req_item in expected_forward:
                        if orig_idx not in synth_map:
                            if rem_idx < len(remaining_resps):
                                synth_map[orig_idx] = remaining_resps[rem_idx]
                                rem_idx += 1

                merged = [synth_map[k] for k in sorted(synth_map.keys())]
                with stdout_lock:
                    sys.stdout.buffer.write((json.dumps(merged, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                    sys.stdout.buffer.flush()
    except Exception:
        pass

def main():
    args = sys.argv[1:]
    rate = 10.0
    burst = 20.0
    child_cmd = []

    i = 0
    while i < len(args):
        if args[i] == "--rate" and i + 1 < len(args):
            rate = float(args[i + 1])
            i += 2
        elif args[i] == "--burst" and i + 1 < len(args):
            burst = float(args[i + 1])
            i += 2
        elif args[i] == "--":
            child_cmd = args[i + 1:]
            break
        else:
            child_cmd = args[i:]
            break

    if not child_cmd:
        sys.stderr.write("Usage: python3 zeromcp_stdio_limiter.py [--rate R] [--burst B] -- <command...>\n")
        sys.exit(1)

    limiter = TokenBucketLimiter(rate, burst)
    stdout_lock = threading.Lock()
    pending_q = queue.Queue()

    proc = subprocess.Popen(
        child_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        bufsize=0
    )

    t = threading.Thread(target=stdout_reader, args=(proc, pending_q, stdout_lock), daemon=True)
    t.start()

    try:
        while True:
            line, oversized = bounded_readline(sys.stdin.buffer, MAX_FRAME_SIZE)
            if line is None and not oversized:
                break

            if oversized:
                err_frame = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": "Frame exceeded maximum allowed size"
                    }
                }
                with stdout_lock:
                    sys.stdout.buffer.write((json.dumps(err_frame, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                    sys.stdout.buffer.flush()
                continue

            stripped = line.strip()
            if not stripped:
                proc.stdin.write(line)
                proc.stdin.flush()
                continue

            try:
                msg = json.loads(
                    stripped.decode("utf-8", errors="surrogatepass"),
                    parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"Constant {c} forbidden"))
                )
            except Exception:
                pending_q.put(('PASSTHROUGH',))
                proc.stdin.write(line)
                proc.stdin.flush()
                continue

            # Case 1: Batch request
            if isinstance(msg, list):
                if len(msg) > MAX_BATCH:
                    err_frame = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "Batch size exceeds maximum of 100"
                        }
                    }
                    with stdout_lock:
                        sys.stdout.buffer.write((json.dumps(err_frame, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                        sys.stdout.buffer.flush()
                    continue

                if not msg:
                    pending_q.put(('PASSTHROUGH',))
                    proc.stdin.write(line)
                    proc.stdin.flush()
                    continue

                synth_map = {}
                forward_items = []
                forward_producing = []

                for idx, item in enumerate(msg):
                    if is_valid_notification(item):
                        if item.get("method") == "tools/call" and not limiter.allow():
                            # Suppress throttled notification silently
                            pass
                        else:
                            forward_items.append(item)
                    elif is_valid_request(item):
                        if item.get("method") == "tools/call" and not limiter.allow():
                            req_id = item["id"]
                            synth_map[idx] = {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": -32000,
                                    "message": f"Rate limit exceeded: tools/call throttled by stdio supervisor (rate={rate}/s, burst={burst})"
                                }
                            }
                        else:
                            forward_items.append(item)
                            forward_producing.append((idx, item))
                    elif not produces_server_response(item):
                        # Client response or non-producing item in batch
                        forward_items.append(item)
                    else:
                        # Malformed request envelope or invalid ID: forward to child to emit -32600 with id: null
                        forward_items.append(item)
                        forward_producing.append((idx, item))

                # Subcase A: No response-producing items were throttled
                if not synth_map:
                    if len(forward_items) < len(msg):
                        # Throttled notifications were filtered out
                        if forward_items:
                            fwd_bytes = (json.dumps(forward_items, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace")
                            if len(fwd_bytes) > MAX_FRAME_SIZE:
                                err_frame = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Frame exceeded maximum allowed size"}}
                                with stdout_lock:
                                    sys.stdout.buffer.write((json.dumps(err_frame, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                                    sys.stdout.buffer.flush()
                                continue
                            if forward_producing:
                                pending_q.put(('PASSTHROUGH',))
                            proc.stdin.write(fwd_bytes)
                            proc.stdin.flush()
                        # If forward_items is empty (only throttled notifications), do nothing
                    else:
                        if forward_producing:
                            pending_q.put(('PASSTHROUGH',))
                        proc.stdin.write(line)
                        proc.stdin.flush()
                    continue

                # Subcase B: All response-producing items were throttled
                if not forward_producing:
                    if forward_items:
                        fwd_bytes = (json.dumps(forward_items, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace")
                        if len(fwd_bytes) <= MAX_FRAME_SIZE:
                            proc.stdin.write(fwd_bytes)
                            proc.stdin.flush()
                    if synth_map:
                        resps = [synth_map[k] for k in sorted(synth_map.keys())]
                        with stdout_lock:
                            sys.stdout.buffer.write((json.dumps(resps, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                            sys.stdout.buffer.flush()
                    continue

                # Subcase C: Mixed batch (some throttled, some forwarded that produce responses)
                fwd_bytes = (json.dumps(forward_items, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace")
                if len(fwd_bytes) > MAX_FRAME_SIZE:
                    err_frame = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Frame exceeded maximum allowed size"}}
                    with stdout_lock:
                        sys.stdout.buffer.write((json.dumps(err_frame, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                        sys.stdout.buffer.flush()
                    continue
                pending_q.put(('BATCH_MERGE', synth_map, forward_producing))
                proc.stdin.write(fwd_bytes)
                proc.stdin.flush()

            # Case 2: Single request / notification / client response
            elif isinstance(msg, dict):
                if is_valid_notification(msg):
                    if msg.get("method") == "tools/call" and not limiter.allow():
                        # Suppress throttled notification silently
                        continue
                    proc.stdin.write(line)
                    proc.stdin.flush()
                elif is_valid_request(msg):
                    if msg.get("method") == "tools/call" and not limiter.allow():
                        err_resp = {
                            "jsonrpc": "2.0",
                            "id": msg["id"],
                            "error": {
                                "code": -32000,
                                "message": f"Rate limit exceeded: tools/call throttled by stdio supervisor (rate={rate}/s, burst={burst})"
                            }
                        }
                        with stdout_lock:
                            sys.stdout.buffer.write((json.dumps(err_resp, separators=(',', ':'), ensure_ascii=False) + "\n").encode("utf-8", errors="backslashreplace"))
                            sys.stdout.buffer.flush()
                        continue
                    pending_q.put(('PASSTHROUGH',))
                    proc.stdin.write(line)
                    proc.stdin.flush()
                else:
                    # Inbound client response, malformed notification, or invalid request envelope/id
                    is_producing = produces_server_response(msg)
                    if is_producing:
                        pending_q.put(('PASSTHROUGH',))
                    proc.stdin.write(line)
                    proc.stdin.flush()

            else:
                pending_q.put(('PASSTHROUGH',))
                proc.stdin.write(line)
                proc.stdin.flush()

    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        t.join()
        ret = proc.wait()
        sys.exit(ret or 0)

if __name__ == "__main__":
    main()
