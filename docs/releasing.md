# Releasing Rivlet

English | [简体中文](./zh-CN/releasing.md)

The distribution, import package, and Rust core crate are named `rivlet`.
The public repository is <https://github.com/ririv/rivlet>. The previous
`rivetoo_graph` import is not retained as an alias.

## Supported distributions

The initial release builds CPython 3.13, 3.14, and 3.15 wheels for Linux x86_64
(glibc 2.28 or later) and macOS arm64 (macOS 11 or later). It also provides a
source distribution. Other platforms require a compatible Rust toolchain and
a source build; they are not claimed as wheel-tested platforms.

Rivlet requires the native extension. There is no Python-only fallback.
The project is pre-1.0; a first public release does not guarantee API stability.

## Validation and publication

`.github/workflows/publish.yml` supports two explicit entry points:

- Manual dispatch builds and tests candidate artifacts without publishing.
- Pushing `v<version>` builds the same artifacts and publishes only after every
  validation job succeeds. The tag, Python version, and Cargo workspace version
  must agree. A failed job prevents the whole release from being uploaded.

Each platform installs its wheel before running the Python test suite in isolated
mode. The source job checks Python and Rust, builds an sdist, builds a wheel from
that sdist, and runs the tests against the installed result. Twine checks the
distribution metadata. Historical benchmarks are not release performance claims.

PyPI uses Trusted Publishing, restricted to repository `ririv/rivlet`, workflow
`publish.yml`, and GitHub environment `pypi`. Only the publishing job receives
`id-token: write`; build jobs have read-only repository access. No long-lived
PyPI token is stored in the repository.

Before the first upload, the PyPI account owner must configure a pending publisher
with project name `rivlet`. This does not reserve the name. A version uploaded
to PyPI cannot be overwritten; fixes require a new release version.
