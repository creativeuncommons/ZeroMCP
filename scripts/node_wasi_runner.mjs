// node_wasi_runner.mjs
// Minimal standalone Node.js WASI Preview 1 runner for ZeroMCP.
// Zero external dependencies.
import { readFile } from "node:fs/promises";
import { WASI } from "node:wasi";
import { fileURLToPath } from "node:url";

const wasmArg = process.argv[2];
const wasmPath = wasmArg && !wasmArg.startsWith("-") 
  ? wasmArg 
  : fileURLToPath(new URL("./zeromcp.wasm", import.meta.url));

const wasiArgs = process.argv.slice(2);
const wasi = new WASI({ 
  version: "preview1", 
  args: wasiArgs, 
  env: {}, 
  stdin: 0, 
  stdout: 1, 
  stderr: 2 
});

const wasmBytes = await readFile(wasmPath);
const { instance } = await WebAssembly.instantiate(wasmBytes, wasi.getImportObject());
wasi.start(instance);
