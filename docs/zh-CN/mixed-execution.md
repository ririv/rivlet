# 实验性混合执行与扩展 SDK

[English](../mixed-execution.md) | 简体中文

**以下整体作为一组实验功能：** Python 图中的 Rust 原生动作与谓词、独立扩展导出、
绑定 SDK、C ABI 桥接、原生值转换及共用原生执行协调层。它们的 API、协议及功能本身
都可能调整或移除。现有入口不移动，不迁入 experimental 命名空间，也不隐式打印警告。
SDK 只通过 `export_node` 导出具体节点；复用声明显式取导入 Graph 的 `.nodes/.edges` 与接口定义。

此标记**不包括**使用 Rust core 编译器的普通 Python 动作/条件、Python 同步与协程混合，
以及纯 Rust 声明和 Execution::run。底层调度器使用 Rust，不等于跨语言动作执行。

## 方便移除的边界

- core/topology 不认识语言对象、桥接回调或外部值 schema。
- 普通 Python session 通过 `host_execution.rs` 直接使用已有 core session。动作、条件、
  动态子图和并发无需进入原生协调层。
- 只有编译表中实际存在 Rust 动作或谓词，才选择 `mixed_execution.rs`。这是显式分发，
  不是执行失败后重试或兜底。
- 协调层独立位于 `crates/execution/src/coordinator`，通过非默认 Cargo feature
  `experimental-mixed-execution` 编译；Python adapter 显式启用，纯 Rust 驱动不需要。
- SDK 与跨库所有权留在 `crates/python-sdk`、`crates/bridge`，不进入 core。通用
  Graph、Step、端口和结果 API 不依赖 SDK 类型。

当前 Python wheel 仍包含实验功能。上述 Cargo feature 是 Rust 模块边界，**不是**
已经实现的“裁剪整个 Python wheel 为纯 Python 动作模式”的开关。

## 移除落点

移除是有界的接线清理，不承诺只删一个目录即可：

- 保留普通 Python 宿主 session 分支，删除私有 session 工厂的原生分支。当前沿用
  `mixed_session` 私有方法名以保持 driver 协议；名称不代表纯宿主 session 有原生任务。
- 在声明适配边界删除原生收集/导入钩子和原生专用展示分支；保留普通 Graph
  快照和通用 API。
- 在共用声明类型中删除私有原生 Step 绑定、导入根谓词分支和 `_from_native` 构造器；
  普通 Python callable 和条件构造保持不变。
- 删除原生结果 codec/包装、Rust 专用编译记录及其导出；这些不是普通 Python 结果存储。
- 删除 SDK capsule 注册、SDK/bridge 依赖、协调层 feature 激活，以及独立的实验模块、
  示例和相关测试。
- core 调度及纯 Rust 高阶驱动保持不变；通用只读访问器和完成结果组合无需撤销。

普通 recipe 或应用不应因此更换 Graph API。显式使用原生子图的消费者需要迁移；
实验性不是把它们静默替换成 Python 或外部进程兜底的理由。

## 验证

```sh
cargo test --locked -p execution --no-default-features
cargo test --locked -p execution --features experimental-mixed-execution
python -m pytest tests/test_experimental_isolation.py
just test-native-bridge
```

第一条检查不包含协调层的纯 Rust 路径。隔离测试检查普通 Python 图和运行时返回的
Python 子图使用 HostExecutionSession，原生声明才使用 MixedExecutionSession；
不将此夸大为整个 Python 构建已经可以删除所有实验源码。

相关入口：[原生图组合](./rust-graphs.md)、[独立扩展](./python-native.md)、
[驱动边界](./design/mixed-driver-boundary.md)。

尚未实现的自主推进器与统一驱动方案，单独记录在[驱动边界讨论](./design/mixed-driver-boundary.md)。
