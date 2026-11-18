import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { copyFile, cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { parseTriple } from "@napi-rs/cli";
import { nativePackageName } from "../lib/native-platform.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const manifest = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
const targets = manifest.napi.targets.map(parseTriple);
const staging = join(root, "scripts", "stage-release.mjs");

test("the loader selects the same identities as napi-rs", () => {
  for (const target of targets) {
    assert.equal(
      nativePackageName(manifest.name, target.platform, target.arch, target.abi === "gnu"),
      `${manifest.name}-${target.platformArchABI}`,
    );
  }
  assert.throws(() => nativePackageName(manifest.name, "linux", "x64", false), /glibc/);
});

test("staging refuses an incomplete matrix without making the root publishable", async t => {
  const check = await mkdtemp(join(tmpdir(), "rivlet-npm-incomplete-"));
  t.after(() => rm(check, { recursive: true }));
  const artifacts = join(check, "artifacts");
  const packaged = join(check, "packaged");
  await mkdir(artifacts);
  const result = spawnSync(process.execPath, [staging, artifacts, packaged], { encoding: "utf8" });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /Missing artifacts for configured targets/);
  assert.equal(JSON.parse(await readFile(join(packaged, "package.json"))).private, true);
});

const glibc = process.platform === "linux" &&
  typeof process.report.getReport().header.glibcVersionRuntime === "string";
const host = targets.find(target => target.platform === process.platform &&
  target.arch === process.arch && (target.platform !== "linux" || glibc));

test("the staged root loads its installed platform package explicitly", {
  skip: !host && "host is not in the release matrix",
}, async t => {
  const check = await mkdtemp(join(tmpdir(), "rivlet-npm-installed-"));
  t.after(() => rm(check, { recursive: true }));
  const artifacts = join(check, "artifacts");
  const modules = join(check, "consumer", "node_modules");
  await mkdir(artifacts);
  await mkdir(modules, { recursive: true });
  for (const target of targets) {
    const artifact = join(artifacts, `${manifest.napi.binaryName}.${target.platformArchABI}.node`);
    if (target === host) {
      await copyFile(join(root, "native", "rivlet.node"), artifact);
    } else {
      // 只占位验证收集与包清单；不把非宿主 fixture 当成平台运行验收。
      await writeFile(artifact, "fixture: exercised only as metadata");
    }
  }
  const packaged = join(modules, manifest.name);
  execFileSync(process.execPath, [staging, artifacts, packaged], { stdio: "pipe" });
  const distribution = JSON.parse(await readFile(join(packaged, "package.json")));
  assert.equal(distribution.private, undefined);
  assert.equal(distribution.scripts, undefined);
  assert.deepEqual(distribution.files, ["lib"]);
  assert.equal(Object.keys(distribution.optionalDependencies).length, targets.length);
  const checkImport = ["--input-type=module", "--eval",
    `import { Step, run } from ${JSON.stringify(manifest.name)};
     const step = new Step(() => 42);
     const outcome = await run(step);
     if (outcome.result(step).unwrap() !== 42) process.exit(2);`];
  const cwd = join(check, "consumer");
  const missing = spawnSync(process.execPath, checkImport, { cwd, encoding: "utf8" });
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /install with optional dependencies enabled/);
  const packageName = nativePackageName(manifest.name, process.platform, process.arch, glibc);
  await cp(join(packaged, "platforms", host.platformArchABI), join(modules, packageName), {
    recursive: true,
  });
  execFileSync(process.execPath, checkImport, { cwd, stdio: "pipe" });
});
