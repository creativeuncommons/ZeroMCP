#!/usr/bin/env python3
"""
ZeroMCP Content-Addressed Storage (CAS) Engine.

Pure standard library, zero-dependency content-addressed offloader for large tool outputs.
When tool results exceed a byte threshold, the payload is offloaded to a local CAS directory,
and a concise token pointer with a structural summary is returned to the agent context.
"""

import os
import sys
import hashlib
import json
import re
import time
import tempfile
from typing import Dict, Any, Optional, Tuple, List

# Default configuration
DEFAULT_CAS_THRESHOLD = int(os.environ.get("ZEROMCP_CAS_THRESHOLD", "4096"))  # 4 KB
CAS_ENABLED_DEFAULT = os.environ.get("ZEROMCP_CAS_ENABLED", "1").lower() not in ("0", "false", "no", "off")

def _resolve_default_storage_dir() -> str:
    """Resolves storage directory defensively, falling back if home dir is sandboxed/unwritable."""
    custom = os.environ.get("ZEROMCP_CAS_DIR")
    if custom:
        return os.path.abspath(os.path.expanduser(custom))

    # Preferred location
    preferred = os.path.expanduser("~/.zeromcp/cas")
    try:
        os.makedirs(preferred, mode=0o700, exist_ok=True)
        with tempfile.TemporaryFile(dir=preferred) as f:
            f.write(b"ok")
        return os.path.abspath(preferred)
    except (OSError, PermissionError):
        pass

    # Workspace-local fallback
    workspace_local = os.path.abspath(".zeromcp_cas")
    try:
        os.makedirs(workspace_local, mode=0o700, exist_ok=True)
        return workspace_local
    except (OSError, PermissionError):
        pass

    # Private per-user tempdir fallback
    return tempfile.mkdtemp(prefix="zeromcp_cas_")

class ZeroMCPCAS:
    """Deterministic, atomic Content-Addressed Storage engine."""

    def __init__(self, storage_dir: Optional[str] = None, threshold_bytes: int = DEFAULT_CAS_THRESHOLD, enabled: bool = CAS_ENABLED_DEFAULT):
        if storage_dir:
            self.storage_dir = os.path.abspath(os.path.expanduser(storage_dir))
        else:
            self.storage_dir = _resolve_default_storage_dir()
        self.threshold_bytes = max(0, threshold_bytes)
        self.enabled = enabled
        if self.enabled:
            try:
                os.makedirs(self.storage_dir, exist_ok=True)
            except (OSError, PermissionError):
                # Fall back to temp dir if custom dir creation failed
                self.storage_dir = os.path.join(tempfile.gettempdir(), "zeromcp_cas")
                os.makedirs(self.storage_dir, exist_ok=True)

    def _path_for_hash(self, sha256_hex: str) -> str:
        """Computes sharded storage path: <storage_dir>/<first_2_hex>/<sha256>.txt"""
        clean_hex = sha256_hex.lower().replace("cas://sha256/", "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", clean_hex):
            raise ValueError(f"Invalid SHA-256 hex digest: {sha256_hex!r}")
        shard = clean_hex[:2]
        shard_dir = os.path.join(self.storage_dir, shard)
        os.makedirs(shard_dir, mode=0o700, exist_ok=True)
        return os.path.join(shard_dir, f"{clean_hex}.txt")

    def store(self, content: str) -> Tuple[str, str, int, bool]:
        """
        Stores content into CAS if enabled and exceeds threshold.
        Returns: (sha256_hex, file_path, byte_count, was_stored)
        """
        raw_bytes = content.encode("utf-8")
        byte_count = len(raw_bytes)
        sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
        file_path = self._path_for_hash(sha256_hex)

        if not self.enabled or byte_count < self.threshold_bytes:
            return sha256_hex, file_path, byte_count, False

        # Atomic write to avoid partial reads under concurrent access
        if not os.path.exists(file_path):
            temp_path = f"{file_path}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
            try:
                with open(temp_path, "wb") as f:
                    f.write(raw_bytes)
                os.replace(temp_path, file_path)
            except Exception as e:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                sys.stderr.write(f"[ZeroMCP-CAS] Warning: Failed to store CAS blob: {e}\n")
                return sha256_hex, file_path, byte_count, False

        return sha256_hex, file_path, byte_count, True

    def retrieve(self, sha256_or_uri: str, offset: int = 0, limit: int = 4096, mode: str = "bytes") -> Dict[str, Any]:
        """
        Surgically retrieves a slice of an offloaded CAS payload.
        mode can be 'bytes' or 'lines'.
        """
        clean_hex = sha256_or_uri.lower().replace("cas://sha256/", "").strip()
        if not re.match(r"^[0-9a-f]{64}$", clean_hex):
            return {"error": f"Invalid SHA-256 hash: '{sha256_or_uri}'", "found": False}

        file_path = self._path_for_hash(clean_hex)
        if not os.path.exists(file_path):
            return {"error": f"CAS blob not found for hash: {clean_hex}", "found": False}

        try:
            with open(file_path, "rb") as f:
                raw_bytes = f.read()
            total_bytes = len(raw_bytes)
            text = raw_bytes.decode("utf-8", errors="replace")

            if mode == "lines":
                lines = text.splitlines(keepends=True)
                total_lines = len(lines)
                start_line = max(0, offset)
                slice_lines = lines[start_line : start_line + max(1, limit)]
                returned_text = "".join(slice_lines)
                return {
                    "hash": clean_hex,
                    "uri": f"cas://sha256/{clean_hex}",
                    "found": True,
                    "mode": "lines",
                    "offset": start_line,
                    "limit": limit,
                    "total_lines": total_lines,
                    "total_bytes": total_bytes,
                    "returned_lines": len(slice_lines),
                    "content": returned_text
                }
            else:
                # Default bytes mode
                start_byte = max(0, offset)
                end_byte = min(total_bytes, start_byte + max(1, limit))
                sliced_bytes = raw_bytes[start_byte:end_byte]
                returned_text = sliced_bytes.decode("utf-8", errors="replace")
                return {
                    "hash": clean_hex,
                    "uri": f"cas://sha256/{clean_hex}",
                    "found": True,
                    "mode": "bytes",
                    "offset": start_byte,
                    "limit": limit,
                    "total_bytes": total_bytes,
                    "returned_bytes": len(sliced_bytes),
                    "content": returned_text
                }
        except Exception as e:
            return {"error": f"Failed reading CAS blob: {e}", "found": False}

    def search(self, sha256_or_uri: str, query: str, max_matches: int = 10, context_lines: int = 2) -> Dict[str, Any]:
        """Searches within an offloaded CAS payload without loading all lines into context."""
        ret = self.retrieve(sha256_or_uri, offset=0, limit=1000000, mode="lines")
        if not ret.get("found"):
            return ret

        lines = ret.get("content", "").splitlines()
        pattern = re.compile(re.escape(query), re.IGNORECASE)

        matches: List[Dict[str, Any]] = []
        for idx, line in enumerate(lines):
            if pattern.search(line):
                start_c = max(0, idx - context_lines)
                end_c = min(len(lines), idx + context_lines + 1)
                context = [
                    {"line_number": c_idx + 1, "text": lines[c_idx], "is_match": c_idx == idx}
                    for c_idx in range(start_c, end_c)
                ]
                matches.append({
                    "match_line": idx + 1,
                    "matched_text": line,
                    "context": context
                })
                if len(matches) >= max_matches:
                    break

        return {
            "hash": ret["hash"],
            "uri": ret["uri"],
            "found": True,
            "query": query,
            "total_matches_found": len(matches),
            "matches": matches,
            "truncated": len(matches) >= max_matches
        }

    def format_pointer_summary(self, sha256_hex: str, file_path: str, raw_content: str) -> str:
        """Renders an LLM-friendly, compact token pointer and preview."""
        byte_count = len(raw_content.encode("utf-8"))
        approx_tokens = byte_count // 4
        preview_head = raw_content[:280].strip()
        preview_tail = raw_content[-120:].strip() if len(raw_content) > 350 else ""

        summary_lines = [
            f"[ZeroMCP-CAS: Payload Offloaded to Content-Addressed Store]",
            f"• Reference: cas://sha256/{sha256_hex}",
            f"• Size: {byte_count:,} bytes (~{approx_tokens:,} tokens saved)",
            f"• Local Path: {file_path}",
            f"• Preview (Head):",
            f"  {preview_head}"
        ]
        if preview_tail:
            summary_lines.extend([
                f"• Preview (Tail):",
                f"  ... {preview_tail}"
            ])
        summary_lines.extend([
            f"",
            f"💡 Content was offloaded to prevent context bloat. To inspect sections:",
            f"   - Tool 'cas_fetch': params {{'hash': '{sha256_hex}', 'offset': 0, 'limit': 1000}}",
            f"   - Tool 'cas_search': params {{'hash': '{sha256_hex}', 'query': 'keyword'}}"
        ])
        return "\n".join(summary_lines)

    def stats(self) -> Dict[str, Any]:
        """Returns statistics on current CAS storage directory."""
        if not os.path.exists(self.storage_dir):
            return {"enabled": self.enabled, "total_blobs": 0, "total_bytes": 0, "storage_dir": self.storage_dir}

        total_files = 0
        total_size = 0
        try:
            for root, _, files in os.walk(self.storage_dir):
                for f in files:
                    if f.endswith(".txt"):
                        fp = os.path.join(root, f)
                        total_files += 1
                        total_size += os.path.getsize(fp)
        except Exception:
            pass

        return {
            "enabled": self.enabled,
            "threshold_bytes": self.threshold_bytes,
            "total_blobs": total_files,
            "total_bytes": total_size,
            "storage_dir": self.storage_dir
        }

    def gc(self, max_age_days: int = 7, dry_run: bool = False) -> Dict[str, Any]:
        """Garbage collects CAS files older than max_age_days."""
        now = time.time()
        cutoff = now - (max_age_days * 86400)
        reclaimed_bytes = 0
        removed_count = 0

        for root, _, files in os.walk(self.storage_dir):
            for f in files:
                if f.endswith(".txt"):
                    fp = os.path.join(root, f)
                    try:
                        stat = os.stat(fp)
                        if stat.st_mtime < cutoff:
                            reclaimed_bytes += stat.st_size
                            removed_count += 1
                            if not dry_run:
                                os.unlink(fp)
                    except OSError:
                        continue

        return {
            "dry_run": dry_run,
            "max_age_days": max_age_days,
            "removed_count": removed_count,
            "reclaimed_bytes": reclaimed_bytes
        }

# Global singleton instance for zero-config import
global_cas = ZeroMCPCAS()
