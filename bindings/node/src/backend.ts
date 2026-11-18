import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { arch, platform, report } from "node:process";
import { nativePackageName } from "./native-platform.js";
import type { NativeModule } from "./protocol.js";

const require = createRequire(import.meta.url);
const metadata: unknown = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Invalid Rivlet package metadata");
  }
  return value as Record<string, unknown>;
}

function nativeEntry(manifest: Record<string, unknown>, version: string): string {
  const config = record(manifest.rivletNative);
  switch (config.kind) {
    case "workspace":
      return "../native/rivlet.node";
    case "registry": {
      if (typeof manifest.name !== "string") {
        throw new Error("Invalid Rivlet package name");
      }
      const diagnostics = platform === "linux" ? record(report.getReport()) : null;
      const glibc = diagnostics !== null &&
        typeof record(diagnostics.header).glibcVersionRuntime === "string";
      const name = nativePackageName(manifest.name, platform, arch, glibc);
      if (record(manifest.optionalDependencies)[name] !== version) {
        throw new Error(`This Rivlet release has no native addon for ${name}`);
      }
      try {
        return require.resolve(name);
      } catch (error) {
        throw new Error(
          `Rivlet requires the native package ${name}@${version}; install with optional dependencies enabled`,
          { cause: error },
        );
      }
    }
    default:
      throw new Error("Invalid Rivlet native package configuration");
  }
}

function loadNative(): NativeModule {
  const manifest = record(metadata);
  const version = manifest.version;
  if (typeof version !== "string") {
    throw new Error("Invalid Rivlet package version");
  }
  // 这是原生 ABI 的唯一类型边界；开发与发行由各自的包清单明确选择。
  const loaded: unknown = require(nativeEntry(manifest, version));
  const addon = loaded as NativeModule;
  if (addon.packageVersion() !== version) {
    throw new Error(
      "JavaScript package and native Rivlet addon versions must agree",
    );
  }
  return addon;
}

export const native = loadNative();
