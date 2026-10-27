# Rivlet 发行说明

[English](../releasing.md) | 简体中文

发行包、Python 导入包和 Rust 核心 crate 统一叫 `rivlet`，公开仓库为
<https://github.com/ririv/rivlet>。不保留旧的 `rivetoo_graph` 导入别名。

## 交付范围

首批 wheel 覆盖 CPython 3.13、3.14、3.15，以及 Linux x86_64（glibc 2.28 起）和
macOS arm64（macOS 11 起）。同时提供源码包。其它平台需要兼容的 Rust 工具链自行
编译源码，不能据此声称这些平台已经通过 wheel 验收。

原生扩展是必需依赖，没有纯 Python 兜底实现。项目尚处于 1.0 之前；首次公开发布
不等于承诺 API 已稳定。

## 验证与发布

`.github/workflows/publish.yml` 提供两个明确入口：

- 手动触发只生成并验证候选产物，不上传 PyPI。
- 推送 `v<version>` 标签执行相同验证，全部通过后才上传。标签、Python 项目版本和
  Cargo workspace 版本必须一致；任何验证失败都会阻止整个发行上传。

每个平台先安装 wheel，再以 Python 隔离模式运行测试，避免误用 checkout 内的源码。
源码任务检查 Python 和 Rust，生成 sdist，再从 sdist 构建并安装 wheel，运行测试。
Twine 检查发行元数据。历史基准数据不作为本轮发行的性能保证。

PyPI 使用 Trusted Publishing，绑定 `ririv/rivlet` 仓库、`publish.yml` 工作流和
`pypi` GitHub environment。只有发布任务取得 `id-token: write`，构建任务只有仓库
读取权限，不保存长期 PyPI token。

首次上传前，PyPI 账户所有者需要配置项目名为 `rivlet` 的 pending publisher。
配置本身不会占用名称。已经上传的版本不能覆盖；修复必须发布新版本。
