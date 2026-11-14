#!/usr/bin/env python3
"""
ZeroMCP Local Distribution & Release Packager
=============================================
Assembles standalone single-file packages, builds available local binaries,
and generates SHA-256 checksums in the dist/ folder.
"""

import os
import sys
import shutil
import hashlib
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")

def compute_sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    os.makedirs(DIST_DIR, exist_ok=True)
    print(f"📦 ZeroMCP Packaging Pipeline -> {DIST_DIR}")

    # 1. Package Python Reference
    py_src = os.path.join(PROJECT_ROOT, "zeromcp-py", "zeromcp.py")
    py_dst = os.path.join(DIST_DIR, "zeromcp.py")
    shutil.copy2(py_src, py_dst)
    os.chmod(py_dst, 0o755)
    print(f"  ✅ Staged Python Reference: {os.path.basename(py_dst)}")

    # 2. Package TypeScript Reference
    ts_src = os.path.join(PROJECT_ROOT, "zeromcp-ts", "zeromcp.ts")
    ts_dst = os.path.join(DIST_DIR, "zeromcp.ts")
    shutil.copy2(ts_src, ts_dst)
    os.chmod(ts_dst, 0o755)
    print(f"  ✅ Staged TypeScript Reference: {os.path.basename(ts_dst)}")

    # 2b. Package WASI Runner
    wasi_src = os.path.join(PROJECT_ROOT, "scripts", "node_wasi_runner.mjs")
    if not os.path.exists(wasi_src):
        wasi_src = os.path.join(PROJECT_ROOT, "node_wasi_runner.mjs")
    wasi_dst = os.path.join(DIST_DIR, "node_wasi_runner.mjs")
    shutil.copy2(wasi_src, wasi_dst)
    print(f"  ✅ Staged WASI Runner: {os.path.basename(wasi_dst)}")

    # 2c. Package Stdio Rate Limiter
    limiter_src = os.path.join(PROJECT_ROOT, "scripts", "zeromcp_stdio_limiter.py")
    limiter_dst = os.path.join(DIST_DIR, "zeromcp_stdio_limiter.py")
    shutil.copy2(limiter_src, limiter_dst)
    os.chmod(limiter_dst, 0o755)
    print(f"  ✅ Staged Stdio Rate Limiter: {os.path.basename(limiter_dst)}")

    # 3. Check for Rust compilation
    cargo_bin = shutil.which("cargo")
    if cargo_bin:
        print(f"  🦀 Found Cargo ({cargo_bin}), compiling native release binary...")
        try:
            subprocess.run(
                [cargo_bin, "build", "--release"],
                cwd=os.path.join(PROJECT_ROOT, "zeromcp-rs"),
                check=True
            )
            rs_src = os.path.join(PROJECT_ROOT, "zeromcp-rs", "target", "release", "zeromcp")
            if os.path.exists(rs_src):
                rs_dst = os.path.join(DIST_DIR, "zeromcp")
                shutil.copy2(rs_src, rs_dst)
                os.chmod(rs_dst, 0o755)
                print(f"  ✅ Built & Staged Native Rust Binary: {os.path.basename(rs_dst)}")

            wasm_src = os.path.join(PROJECT_ROOT, "zeromcp-rs", "target", "wasm32-wasip1", "release", "zeromcp.wasm")
            if not os.path.exists(wasm_src):
                wasm_src = os.path.join(PROJECT_ROOT, "zeromcp-rs", "target", "wasm32-wasi", "release", "zeromcp.wasm")
            if os.path.exists(wasm_src):
                wasm_dst = os.path.join(DIST_DIR, "zeromcp.wasm")
                shutil.copy2(wasm_src, wasm_dst)
                print(f"  ✅ Staged WebAssembly WASI Binary: {os.path.basename(wasm_dst)}")
        except Exception as e:
            print(f"  ⚠️ Cargo build skipped or failed: {e}")
    else:
        print("  ℹ️ Cargo not installed locally. Native binaries are built via GitHub Actions CI workflow.")

    # 4. Generate SHA256 Checksums
    checksums = []
    for fname in sorted(os.listdir(DIST_DIR)):
        if fname == "SHA256SUMS.txt" or fname.startswith("."):
            continue
        fpath = os.path.join(DIST_DIR, fname)
        if os.path.isfile(fpath):
            sha = compute_sha256(fpath)
            checksums.append(f"{sha}  {fname}")

    checksum_file = os.path.join(DIST_DIR, "SHA256SUMS.txt")
    with open(checksum_file, "w", encoding="utf-8") as f:
        f.write("\n".join(checksums) + "\n")

    print(f"\n🔐 Generated SHA-256 Checksums ({len(checksums)} files):")
    for line in checksums:
        print(f"    {line}")
    print("\n🚀 Distribution packaging complete.")

if __name__ == "__main__":
    main()
