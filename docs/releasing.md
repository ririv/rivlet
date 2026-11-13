# Releasing Rivlet

English | [简体中文](./zh-CN/releasing.md)

The distribution, import package, and Rust core crate are named `rivlet`.
The public repository is <https://github.com/ririv/rivlet>. The previous
`rivetoo_graph` import is not retained as an alias.

## Supported distributions

The release matrix targets CPython 3.13, 3.14, and 3.15 on Linux x86_64
(glibc 2.28 or later), macOS arm64 (macOS 11 or later), and Windows x86_64
(`win_amd64`, MSVC). It also provides a source distribution. Other platforms
require a compatible Rust toolchain and a source build; they are not claimed as
wheel-tested platforms. Windows ARM64 and free-threaded Python are not part of
this matrix.

Rivlet requires the native extension. There is no Python-only fallback.
The project is pre-1.0; a first public release does not guarantee API stability.

## Versioning

During 0.x development, new public graph capabilities and breaking public API
changes increment the minor version; compatible fixes increment the patch version.
Published files are never replaced.

Documentation or distribution-metadata updates that do not change the library use
a PEP 440 post-release, such as `0.2.0.post1`. The Python distribution and tag
(`v0.2.0.post1`) include the suffix; Rust crates keep the base version (`0.2.0`).
This is a new distribution, not an overwrite of 0.2.0. Actual software fixes must
use a new patch or minor version instead of a post-release. Run
`python scripts/check_release.py` to validate the current manifests, or pass
`--tag v0.3.0` to check the proposed current tag. The script supports final releases
and their canonical `.postN` forms; it rejects unsupported version forms.

Version 0.3.0 adds graph-declared static selection with `static.select()` and
`Case(condition, component)`. All candidates remain inspectable in the declaration;
compilation requires exactly one true condition in each active selection. There
is no branch-order precedence, implicit fallback, or selector action. See
[static selection](./static_selection.md) for composition and port boundaries.
Existing evaluator-bound `StaticConditions` rules remain available.

The same release adds versioned JSON structural export through `export_graph()`,
with declaration and compiled views, typed edge provenance, mounted identities,
interface ports, and explicit static exclusions. Execution prepares one
successful compilation and reuses it for compile/run/arun; its bindings are frozen.
Use a new execution for fresh static decisions. See [structural export](./graph_export.md).

This version does not add JavaScript, Swift, or Java bindings, cross-language
action orchestration, or browser support.

Version 0.2.0 adds typed direct failure and blockage sources. Node event consumers
must replace `event.phase` with `event.state` and `StepExecutionPhase` with
`NodeExecutionState`. Event construction uses `state_detail=`; its value also
determines the read-only `state`. There are no old-name aliases. See
[failure events](./failure_events.md) for the complete model and migration boundary.
This release adds no checkpoint restoration, automatic retry, or event numbering.

## Validation and publication

Adding a matrix target is not evidence that its artifacts have passed: release
only after a successful build and installed-wheel validation on the target
operating system. Cross-built wheels must also pass native target validation;
cross-compilation alone is not a runtime test. See
[Windows setup](./development.md#windows-setup) for local builds and the same
validation entry point used by CI; Actions is not required for local validation
or token-based publication.

`.github/workflows/publish.yml` supports two explicit entry points:

- Manual dispatch builds and tests candidate artifacts without publishing.
- Pushing `v<version>` builds the same artifacts and publishes only after every
  validation job succeeds. The tag must match the Python version, and the Cargo
  workspace must match its base version, excluding any `.postN` suffix.
  A failed job prevents the whole release from being uploaded.

Each platform installs its wheel before running the Python test suite in isolated
mode. The source job checks Python and Rust, builds an sdist, builds a wheel from
that sdist, and runs the tests against the installed result. Twine checks the
distribution metadata. Historical benchmarks are not release performance claims.

`scripts/verify_wheel.py` accepts a directory containing exactly one wheel and
runs the installed-artifact checks with the current Python interpreter. Use a
fresh virtual environment and separate output directory for each platform/Python
combination. It does not expand shell wildcards, install dependencies implicitly,
or publish. Install `pytest` and `twine` explicitly before running it.

PyPI uses Trusted Publishing, restricted to repository `ririv/rivlet`, workflow
`publish.yml`, and GitHub environment `pypi`. Only the publishing job receives
`id-token: write`; build jobs have read-only repository access. No long-lived
PyPI token is stored in the repository.

When Actions is unavailable, publication can be performed locally only after the
distributions being uploaded have passed their build and installed-artifact checks.
Additional validated wheels may be appended to the same release later; published
filenames are never overwritten.
Record the source commit, interpreter/toolchain versions, distribution hashes,
and installed-artifact test results. Use a project-scoped PyPI token stored outside
the repository with restricted access; never place it in release notes or artifacts.
Do not treat local token authentication as a GitHub OIDC provenance attestation.

Before the first upload, the PyPI account owner must configure a pending publisher
with project name `rivlet`. This does not reserve the name. A version uploaded
to PyPI cannot be overwritten; fixes require a new release version.
