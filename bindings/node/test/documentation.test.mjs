import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { promisify } from "node:util";

const execute = promisify(execFile);
const library = new URL("../lib/index.js", import.meta.url).href;

for (const [path, heading, expected] of [
  ["../../../docs/node-api.md", "Subgraphs and public ports", "42"],
  ["../../../docs/zh-CN/node-api.md", "子图与公开端口", "42"],
  ["../../../docs/zh-CN/cancellation.md", "Node.js", "cancelled"],
]) {
  test(`文档示例可独立执行：${path}`, async () => {
    const document = await readFile(new URL(path, import.meta.url), "utf8");
    const section = document.split(`## ${heading}\n`)[1]?.split("\n## ")[0];
    assert.ok(section, `缺少章节：${heading}`);
    const example = /```(?:js|javascript)\n([\s\S]*?)\n```/.exec(section)?.[1];
    assert.ok(example, "缺少可执行 JavaScript 示例");
    // 执行文档原文，只将发行包导入地址绑定到本次构建，不复制一份示例逻辑。
    assert.ok(example.includes("'./lib/index.js'"));
    const source = example.replace("'./lib/index.js'", JSON.stringify(library));
    const { stdout, stderr } = await execute(
      process.execPath,
      ["--input-type=module", "--eval", source],
      { timeout: 10_000 },
    );
    assert.equal(stdout.trim(), expected);
    assert.equal(stderr, "");
  });
}
