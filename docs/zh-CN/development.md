# 开发 Rivlet

[English](../development.md) | 简体中文

本文面向修改 Rivlet 自身的开发者。只使用库时，通过 `pip install rivlet` 安装
发行包，从 [README](../../README.zh-CN.md) 开始即可。

## 本地环境

需要 Python 3.13 或更高版本，以及兼容锁定依赖的 Rust 工具链。当前发行流程使用
Rust 1.95.0 验证。

```bash
git clone https://github.com/ririv/rivlet.git
cd rivlet
python3 -m venv .venv
source .venv/bin/activate
python -m pip install 'maturin>=1.9,<2' 'pytest>=8' 'ruff>=0.14.14' 'pyright[nodejs]>=1.1.409'
maturin develop --release --locked
```

最后一条命令构建必需的原生扩展，并以 editable 方式安装 Python 包。
修改 Rust 代码后需要重新构建扩展，不提供纯 Python 执行兜底。

## 检查

激活虚拟环境后执行：

```bash
python scripts/check_release.py
ruff check src scripts tests benchmarks
pyright src scripts
python -m pytest tests
cargo test --locked --workspace --exclude python
cargo clippy --locked --workspace --all-targets -- -D warnings
cargo fmt --check
```

日常修改运行直接相关的测试；修改公共编译或执行逻辑时，应扩大到完整图测试。

## 延伸阅读

- [架构及 Python／Rust 边界](./rust-architecture.md)
- [编译复杂度](./topology_complexity.md)
- [交付验证与发行流程](./releasing.md)
- [文档目录](./index.md)

文档默认英文。调整公开 API 示例或行为时，同步更新 `docs/zh-CN/` 对应页面与
中英文 README。
