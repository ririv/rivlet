import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import type { NativeModule } from "./protocol.js";

// 此处是唯一的原生 ABI 类型边界；缺少构建产物就直接报错，不切换到 JS/Python 实现。
const loaded: unknown = createRequire(import.meta.url)("../native/rivlet.node");
export const native = loaded as NativeModule;
const metadata: unknown = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);
if (
  typeof metadata !== "object" ||
  metadata === null ||
  !("version" in metadata) ||
  metadata.version !== native.packageVersion()
) {
  throw new Error(
    "JavaScript package and native Rivlet addon versions must agree; rebuild the addon",
  );
}
