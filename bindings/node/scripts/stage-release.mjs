import { execFileSync } from "node:child_process";
import { copyFile, mkdir, readFile, stat, unlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { NapiCli, parseTriple } from "@napi-rs/cli";

const here = fileURLToPath(new URL("..", import.meta.url));
const [, , input, output, ...extra] = process.argv;
if (!input || !output || extra.length !== 0) {
  throw new Error("Usage: npm run stage:release -- <artifacts-dir> <new-output-dir>");
}
const artifacts = resolve(input);
const cwd = resolve(output);
const source = JSON.parse(await readFile(join(here, "package.json"), "utf8"));
const targets = source.napi.targets.map(parseTriple);
const npmDir = "platforms";
const optionalDependencies = Object.fromEntries(
  targets.map(target => [`${source.name}-${target.platformArchABI}`, source.version]),
);
const manifest = {
  name: source.name,
  version: source.version,
  private: true,
  description: source.description,
  license: source.license,
  repository: source.repository,
  type: source.type,
  engines: source.engines,
  exports: source.exports,
  files: source.files,
  napi: source.napi,
  optionalDependencies,
  publishConfig: { access: "public" },
  rivletNative: { kind: "registry" },
};

// 只接受新目录，避免混入旧产物；部分完成的主包保持私有。
await mkdir(cwd);
const packageJson = join(cwd, "package.json");
await writeFile(packageJson, JSON.stringify(manifest, null, 2) + "\n");
const cli = new NapiCli();
await cli.createNpmDirs({ cwd, npmDir });
await cli.artifacts({ cwd, npmDir, outputDir: artifacts });
for (const target of targets) {
  const binary = `${source.napi.binaryName}.${target.platformArchABI}.node`;
  const platform = join(cwd, npmDir, target.platformArchABI);
  const metadata = JSON.parse(await readFile(join(platform, "package.json"), "utf8"));
  const libc = target.abi === "gnu" ? ["glibc"] : undefined;
  if (metadata.version !== source.version || metadata.main !== binary ||
      metadata.name !== `${source.name}-${target.platformArchABI}` ||
      !sameArray(metadata.os, [target.platform]) ||
      !sameArray(metadata.cpu, [target.arch]) ||
      !sameArray(metadata.libc, libc)) {
    throw new Error(`Invalid generated npm identity for ${target.triple}`);
  }
  if (!(await stat(join(platform, binary))).isFile()) {
    throw new Error(`Missing packaged native artifact for ${target.triple}`);
  }
  await copyFile(join(here, "LICENSE"), join(platform, "LICENSE"));
  // napi artifacts 也会在根目录保留一份；主包不交付该副本。
  await unlink(join(cwd, binary));
}
const require = createRequire(import.meta.url);
const tsManifest = require.resolve("typescript/package.json");
const { bin: { tsc } } = JSON.parse(await readFile(tsManifest, "utf8"));
execFileSync(process.execPath, [
  resolve(dirname(tsManifest), tsc),
  "--project", join(here, "tsconfig.json"),
  "--outDir", join(cwd, "lib"),
], { stdio: "inherit" });
await copyFile(join(here, "README.md"), join(cwd, "README.md"));
await copyFile(join(here, "LICENSE"), join(cwd, "LICENSE"));
const { private: _private, napi: _napi, ...publicManifest } = manifest;
await writeFile(packageJson, JSON.stringify(publicManifest, null, 2) + "\n");

function sameArray(actual, expected) {
  if (expected === undefined) return actual === undefined;
  return Array.isArray(actual) && actual.length === expected.length &&
    actual.every((value, index) => value === expected[index]);
}
