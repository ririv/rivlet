# Developing Rivlet

English | [简体中文](./zh-CN/development.md)

This guide is for changing Rivlet itself. To use the library, install the
published package with `pip install rivlet` and start with the
[README](../README.md).

## Local setup

Use Python 3.12 or later and a Rust toolchain compatible with the locked
dependencies. The release workflow currently validates Rust 1.95.0.

```bash
git clone https://github.com/ririv/rivlet.git
cd rivlet
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'maturin>=1.9,<2' 'pytest>=8' 'ruff>=0.14.14' 'pyright[nodejs]>=1.1.409'
maturin develop --release --locked
```

The last command builds the required native extension and installs the Python
package in editable mode. Rebuild it after changing Rust code. There is no
Python-only execution fallback.

## Windows setup

Use x64 CPython 3.12–3.15 and the Rust `x86_64-pc-windows-msvc` toolchain.
Building from source also requires Visual Studio Build Tools with the C++ toolset
and a Windows SDK; installing a wheel does not require these build tools.
In PowerShell, use the environment's executables directly; activation is optional:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install 'maturin==1.15.0' 'pytest>=8' 'twine>=6'
.\.venv\Scripts\maturin.exe build --release --locked --target x86_64-pc-windows-msvc --interpreter .\.venv\Scripts\python.exe --out dist
.\.venv\Scripts\python.exe scripts/verify_wheel.py --dist dist --junitxml windows-results.xml
cargo +1.95.0 test --locked --workspace --exclude python
```

Run this in a fresh checkout and environment for each Python version. `dist` must
contain exactly one wheel for that interpreter. The shared verification script
checks metadata, installs the exact wheel, verifies the installed native extension
in isolated mode, and runs the full Python suite. It works without shell globbing.
It never uploads artifacts. Python subprocesses, dynamic graphs, native actions,
their independent worker resources, and optional shared admission limits are covered
by the same cross-platform tests. Do not infer target support merely from skipping
platform-specific tests; installed artifacts require native validation.

No Unix shell is supplied implicitly. Native adapters must explicitly provide a
program, an argument vector, a working directory, and the child environment;
choosing a shell, if needed, is the adapter's responsibility.

## Checks

With the virtual environment activated:

```bash
python scripts/check_release.py
ruff check src scripts tests benchmarks
pyright src scripts
python -m pytest tests
cargo test --locked --workspace --exclude python
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo fmt --check
```

During development, run the tests relevant to the changed boundary. Changes to
shared compilation or execution logic warrant the full graph suite.

## Further reading

- [Architecture and Python/Rust boundaries](./rust-architecture.md)
- [Compilation complexity](./topology_complexity.md)
- [Distribution validation and publication](./releasing.md)
- [Documentation index](./index.md)

English is the default documentation language. Keep the Chinese counterpart in
`docs/zh-CN/` and both READMEs aligned when changing public API examples or behavior.
