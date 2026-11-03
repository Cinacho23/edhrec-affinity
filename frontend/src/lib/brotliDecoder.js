import initialize, { decompress } from "brotli-dec-wasm/web";
import wasmUrl from "brotli-dec-wasm/web/bg.wasm?url";

let ready;

export async function decodeBrotli(bytes) {
  ready ??= initialize({ module_or_path: wasmUrl }).catch((error) => {
    ready = undefined;
    throw error;
  });
  await ready;
  return decompress(bytes);
}
