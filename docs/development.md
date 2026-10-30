# Developing Rivlet

English | [简体中文](./zh-CN/development.md)

This guide is for changing Rivlet itself. To use the library, install the
published package with `pip install rivlet` and start with the
[README](../README.md).

## Local setup

Use Python 3.13 or later and a Rust toolchain compatible with the locked
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

## Checks

With the virtual environment activated:

```bash
ruff check src tests benchmarks
pyright src
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
