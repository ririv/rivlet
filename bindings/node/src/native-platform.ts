/** Resolve the napi-rs package identity of a native Node host. */
export function nativePackageName(
  name: string,
  os: string,
  arch: string,
  glibc: boolean,
): string {
  switch (os) {
    case "darwin":
      return `${name}-darwin-${arch}`;
    case "win32":
      return `${name}-win32-${arch}-msvc`;
    case "linux":
      if (glibc) return `${name}-linux-${arch}-gnu`;
      throw new Error(`Rivlet supports only glibc on Linux (${arch})`);
    default:
      throw new Error(`Rivlet has no native addon for ${os}/${arch}`);
  }
}
