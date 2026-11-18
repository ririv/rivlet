# Releasing Rivlet

English | [简体中文](./zh-CN/releasing.md)

The Python distribution, import package, and public Rust entry crate are named `rivlet`.
The public repository is <https://github.com/ririv/rivlet>. The previous
`rivetoo_graph` import is not retained as an alias.

## Supported distributions

The release matrix targets CPython 3.12, 3.13, 3.14, and 3.15 on Linux x86_64
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
`--tag v0.4.0` to check the proposed current tag. The script supports final releases
and their canonical `.postN` forms; it rejects unsupported version forms.

## Rust source releases

Future Rust releases package only `rivlet-core` and `rivlet`. Verify both
source packages, publish `rivlet-core` first, and wait until crates.io indexes
it before publishing `rivlet`. Rust users only need `rivlet`; the Python and
Node.js bindings continue to use only `rivlet-core`.

```sh
cargo package --locked -p rivlet-core -p rivlet
```

The published Rust 0.5.0 remains unchanged. When upgrading, direct users of the
former component crates should use `rivlet::{action, graph, execution}` or
`rivlet::core`. Users already importing through `rivlet` do not need to change
their imports.

## Version history

### Version 0.5.0

Graph directly accepts nodes, edges, interface definitions and static declarations.
The separate GraphStructure declaration is removed without a compatibility alias.
This breaking change requires a new minor release; existing published files stay unchanged.

Mixed Python/Rust execution remains outside mainline. Python supports awaitable
runtime predicates and [external resource admission](./zh-CN/resource_admission.md).
Rust consumers import `Graph`, `Step` and `Execution` from `rivlet`; the low-level
compiler and scheduler are available through `rivlet::core`. Rust 1.88 is the
minimum supported version. The npm root and its native platform packages share a
version and do not require Python.

Publish the channels separately after validating their artifacts. Run
`python scripts/check_release.py --all-channels --tag v0.5.0` from the checkout
to verify the synchronized release identity.

### Version 0.4.0

This release changes public APIs; it is not a drop-in replacement for 0.3.0.

Python 3.12 is the minimum supported version. Default Rust worker capacity is
selected by the managed Rust session, without importing Python 3.13 CPU APIs.

- `Step` is an action node and `Graph` is a composite node. Both implement the
  common Node protocol; `GraphStructure` owns reusable topology. Execute either
  node through `Execution`, rather than wrapping a graph in an action step.
- Graph-owned input/output references support explicit readiness and result
  connections. String names and anonymous `None` endpoints can coexist;
  `.interface.input` / `.output` select only the explicitly anonymous endpoint.
  See [interfaces](./interfaces.md).
- Declare positional result transport with `Edge.results(...)` or `Results(...)`
  in wiring. Runtime predicates accept `condition=(predicate, producer, ...)`;
  action context no longer provides arbitrary cross-node result lookup. Results
  remain `Option` values. See [core model](./core_model.md).
- `run` / `arun` own their execution resources; low-level `Execution.run` / `arun`
  borrow a thread pool. `max_workers` limits blocking Python workers, while
  optional execution-level `max_concurrency` scopes constrain descendant actions
  across synchronous, asynchronous, and Rust work. See [execution](./execution.md).
- Rust graph declarations, typed native result transport, and Rust execution
  share the same graph core with Python. Python conversion is explicit at the
  adapter boundary. See [Rust graphs](./rust-graphs.md) and [Rust results](./rust-results.md).

`@step` still declares an actual Step; a function-call graph frontend is not part
of this release. Use constructors, wiring/DSL, and ordinary graph factory functions.
The removed zero-argument `@graph` decorator is not retained as an alias.
JavaScript, Swift, and Java bindings are not included.

### Version 0.3.0

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
that sdist, and runs the tests against the installed result. Every platform job
downloads that same sdist instead of rebuilding from its checkout. Twine checks the
distribution metadata. Historical benchmarks are not release performance claims.

## Shared local and CI commands

`justfile` is an optional command launcher; `scripts/build_dist.py`,
`scripts/verify_wheel.py`, and `scripts/publish_dist.py` implement the workflow.
Actions calls those same scripts directly. Neither Just nor Actions defines a
second build implementation. Run `python scripts/build_dist.py settings` for
the release Rust/Maturin versions and Python matrix. Prepare those tools and
a separate verification environment explicitly; scripts do not install host tools.
The container entry alone bootstraps tools inside its disposable container.

Activate the chosen Python environment before using Just. Its recipes use Python,
not an implicitly supplied Unix shell, including on Windows. From the repository:

```sh
just sdist dist/source
just verify-source dist/source/rivlet-0.4.0.tar.gz dist/receipts

# Linux Docker host: the official manylinux environment, without Zig.
just wheel-linux dist/source/rivlet-0.4.0.tar.gz dist/linux-313 3.13

# Linux without a container: explicitly prepared Zig and CPython interpreters.
just wheel-linux-zig dist/source/rivlet-0.4.0.tar.gz dist/linux-zig-313 python3.13

# Native macOS; use x86_64-pc-windows-msvc on a prepared Windows build host.
just wheel-native dist/source/rivlet-0.4.0.tar.gz dist/macos-313 aarch64-apple-darwin python3.13

just verify-wheel path/to/rivlet.whl dist/receipts
just check-dist path/to/distributions dist/receipts
just publish path/to/distributions dist/receipts
```

The version above is illustrative: use the filename produced from the current
manifests. Updating the workflow does not authorize replacing an existing release.
Output directories must be empty; existing builds and verification records are
never overwritten. Each wheel is tested with its matching target interpreter.
Copy cross-built wheels to the target OS and run verification there; do not write
a success record on the cross-compilation host.

The two Linux entries are alternatives, never an automatic fallback:

- `wheel-linux` launches the official manylinux 2.28 container through Docker;
  the workflow uses this entry and does not need Zig.
- `wheel-linux-zig` runs on Linux and explicitly supplies `--zig --manylinux 2_28`
  to Maturin. Prepare Zig and cargo-zigbuild yourself. Maturin audits the resulting
  library; a plain host build is not relabeled as manylinux.

For direct script usage, `build_dist.py native`, `manylinux`, and `linux-zig`
accept an sdist, an output directory and `--interpreter` followed by interpreter
paths. The native entry also requires `--target`. `container` instead takes
`--python-version`. No SSH host, sudo policy, SDK location or user directory is
embedded in these commands. Cross-compilation environment setup remains explicit;
the workflow does not provision a second machine or a Windows SDK automatically.

Verification writes a JSON receipt only after metadata, isolated installation and
the full test suite pass. It records the artifact's SHA-256, release version and
verification interpreter/platform; the hash is checked again after testing.
`verify-source` records an sdist only after building and testing a wheel from it.
Local upload and CI staging reject artifacts without matching receipts or whose
bytes have changed. Local upload copies checked artifacts to a private temporary
directory before invoking Twine; CI stages the same checked files before Trusted
Publishing. Receipts are local workflow evidence, **not signed provenance**, and
do not protect against a publisher deliberately fabricating them.

Build and verification commands never upload. `just publish` is the explicit
token-based PyPI operation. Configure Twine outside the repository (for example
`~/.pypirc`); unset `TWINE_REPOSITORY_URL` when using this entry so it does not
bypass the named `pypi` repository credentials. Do not put credentials in Just,
receipts, artifacts or scripts. Actions retains its separate OIDC publisher.

The equivalent CI pre-publication gate is:

```sh
python scripts/publish_dist.py stage dist dist/receipts upload
```

It stages only verified distributions, not the receipt files, for the publisher.

`scripts/verify_wheel.py` accepts `--wheel FILE` or `--dist DIRECTORY` containing exactly one wheel and
runs the installed-artifact checks with the current Python interpreter. Use a
fresh virtual environment and separate output directory for each platform/Python
combination. It does not expand shell wildcards, install dependencies implicitly,
or publish. Install `pytest` and `twine` explicitly before running it. Use
`--receipts DIRECTORY` to retain the successful artifact verification record.
`--source-root` explicitly selects the extracted source tests when validating an sdist.

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
