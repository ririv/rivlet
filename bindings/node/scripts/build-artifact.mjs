import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { NapiCli } from "@napi-rs/cli";

const cwd = fileURLToPath(new URL("..", import.meta.url));
const [, , target, directory, ...extra] = process.argv;
if (!target || !directory || extra.length !== 0) {
  throw new Error("Usage: npm run build:artifact -- <rust-target> <new-output-dir>");
}
const manifest = JSON.parse(await readFile(new URL("../package.json", import.meta.url)));
if (!manifest.napi.targets.includes(target)) {
  throw new Error(`Unsupported Rivlet npm release target: ${target}`);
}
const outputDir = resolve(directory);
await mkdir(outputDir);
const { task } = await new NapiCli().build({
  cwd,
  manifestPath: "../../crates/node/Cargo.toml",
  target,
  outputDir,
  platform: true,
  noJsBinding: true,
  dts: "binding.d.ts",
  release: true,
  cargoOptions: ["--locked"],
});
await task;
