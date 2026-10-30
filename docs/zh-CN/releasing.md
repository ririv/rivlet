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

## 版本规则

在 0.x 阶段，破坏公开 API 的变更提升次版本；兼容修复提升修订版本。已经发布的
产物不能覆盖。

不改变库本身的文档或发行元数据更新，使用 PEP 440 后发行版本，例如
`0.2.0.post1`。Python 发行号与标签 `v0.2.0.post1` 带后缀，Rust crate 保持基础版本
`0.2.0`。这是一次新发行，不覆盖已有的 0.2.0。真正的软件修复应提升修订版本或
次版本，不使用后发行号。执行 `python scripts/check_release.py` 检查当前 manifest；
加上 `--tag v0.2.0.post1` 可验证准备创建的标签。脚本支持正式版及其规范的 `.postN`
形式，不接受其它未支持的版本写法。

0.2.0.post1 更新面向使用者的 README，并把开发环境说明移入
[开发指南](./development.md)，不修改库源码、Rust crate 版本或运行行为。

0.2.0 新增强类型的直接失败与阻断来源。步骤事件消费方把 `event.phase` 改为
`event.state`，`StepExecutionPhase` 改为 `StepExecutionState`。构造事件使用
`state_detail=`，只读的 `state` 从中推导，不保留旧名称别名。完整模型与迁移边界见
[失败事件](./failure_events.md)。这次发行没有增加检查点恢复、自动重试或事件序号。

## 验证与发布

`.github/workflows/publish.yml` 提供两个明确入口：

- 手动触发只生成并验证候选产物，不上传 PyPI。
- 推送 `v<version>` 标签执行相同验证，全部通过后才上传。标签必须匹配 Python
  项目版本，Cargo workspace 必须匹配去掉 `.postN` 后的基础版本；任何验证失败
  都会阻止整个发行上传。

每个平台先安装 wheel，再以 Python 隔离模式运行测试，避免误用 checkout 内的源码。
源码任务检查 Python 和 Rust，生成 sdist，再从 sdist 构建并安装 wheel，运行测试。
Twine 检查发行元数据。历史基准数据不作为本轮发行的性能保证。

PyPI 使用 Trusted Publishing，绑定 `ririv/rivlet` 仓库、`publish.yml` 工作流和
`pypi` GitHub environment。只有发布任务取得 `id-token: write`，构建任务只有仓库
读取权限，不保存长期 PyPI token。

首次上传前，PyPI 账户所有者需要配置项目名为 `rivlet` 的 pending publisher。
配置本身不会占用名称。已经上传的版本不能覆盖；修复必须发布新版本。
