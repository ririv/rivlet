# Rust 架构与语言边界

[English](../rust-architecture.md) | 简体中文

`rivlet` 提供 Python 与 Rust 声明 API，正式执行共享 Rust 声明编译器和调度状态机。
Rust 持有完整的编译图、调度索引和单次执行状态；Python 保留任意 callable、
结果对象与宿主集成。

## crate 边界

Rust 发行只需要两个 crate：

- `crates/core`
  - 包名为 `rivlet-core`，由 Python、Node.js 和 Rust 共同使用
  - 持有声明编译、编译执行图、端口解析和调度状态机；整数节点、邻接索引和 DAG 算法位于内部 `topology` 模块
  - 只依赖 Rust 标准库，不解释任何语言的动作或谓词
- `crates/rivlet`
  - `rivlet::action` 提供类型化动作、结果值和进程动作
  - `rivlet::graph` 提供 `Step/Graph`、封闭的 `Node` 枚举，以及类型化端口、边和谓词
  - `rivlet::execution` 组合图、动作和 core，提供 Rust 执行入口和类型化结果
  - 宿主需要低阶接口时显式使用 `rivlet::core`

绑定与实验 crate 不发布到 crates.io：

- `crates/python`
  - 私有 Python adapter
  - 独占 PyO3 依赖
  - 把 Python 声明对象批量转换成连续整数 ID
  - 把条件 predicate、Python action、观察事件和宿主停止控制接回 Python
- `crates/tokio-runtime`
  - 私有实验 crate，不属于正式 API
  - 独占 Tokio 依赖，用于测量“Rust runtime 逐 action 回调 Python”的成本
  - 只通过项目自己的 `CallbackHost` 接口消费 core，不向领域模型泄漏 Tokio 类型
- `crates/node`
  - 实验性 Node-API adapter，独占 napi-rs/serde 依赖
  - 使用独立 Cargo workspace/锁文件，不属于上面的 Python 发行 workspace
  - 将 JS 声明和批量完成通知接入现有 core 编译器与 session
  - `bindings/node` 保留 JS 回调和任意结果对象，不经过 Python
  - 支持边界见 [Node.js API](./node-api.md)

core 的公开数据模型不出现 PyO3 或其它第三方库类型。

## 语言边界上的命名

Rust 公共类型表达职责，不重复标注实现语言：`action::Action`、`ActionContext`、
`ActionHandle`、`ActionError`、`ActionExecutor`、`Value`、`ValueType`。
有序输入协议叫 `ActionInputs`，与 action 自身的 `Inputs` 关联类型区分。

Python 声明使用 Step/Graph；私有编译和执行绑定按职责命名：
CompiledExecutionGraph、DeclarationCompilation、ExecutionSession。纯 Rust 声明与
执行 API 独立使用，不通过 Python 动作或结果桥接。

二进制扩展模块为 `rivlet._native`；Python 声明使用 core 编译器和 session，
不通过动作或结果桥接接入 Rust 声明。

Rust 类型化结果由纯 Rust 执行层持有，见 [Rust 结果值](./rust-results.md)。

## 外部库约束

核心 crate 不直接依赖第三方图、调度或并发库。确实需要外部实现时，新增独立
adapter crate，由该 crate 独占外部依赖，并通过项目自身定义的窄接口接入 core。

例如未来若采用第三方 work-stealing runtime，应形成：

```text
core Scheduler 接口
        ▲
        │ 项目自有模型
executor adapter crate
        │
        ▼
第三方 runtime
```

不得让第三方节点、任务、错误或 executor 类型进入 Graph 的领域模型和公开 API。
移除或替换第三方实现时，应用的 Graph 声明和 core 模型不应跟着修改。

## 当前执行边界

Python Step/Graph 经 PyO3 批量接入 core 声明编译器。core 持有执行依赖、层级、
就绪判断和状态转换；Python driver 管理自己的 callable、线程池、asyncio、结果和异常。
Python 运行时条件可以同步返回 bool 或返回 awaitable，不占动作 worker 名额。

Python driver 使用 core 的 start/advance 交换批量动作、条件和事件，没有逐节点回调。
运行时返回的 Graph 物化后仍进入同一个编译器；Python 协调动态 session，共用请求的
线程池和事件循环。父动作等待子图期间不占动作名额。显式子图限额使用 core 的共享
计数，动态子图继承父范围，不另设一套限额算法。

纯 Rust Step/Graph 由 `rivlet::graph` 投影，再进入同一 core 编译器；`rivlet::execution`
提供同步 Execution::run，拥有自己的 Rust 动作执行和类型化结果，不依赖 Python。
Node.js 前端同样独立接入 core，使用自己的 JS 回调和事件循环。

主线没有 RustGraph、原生结果 codec、SDK 或跨语言混合执行入口。完整实验
保留在 codex/experimental-mixed-execution 分支，不属于当前主线 API。

## 运行时实验

私有 `crates/tokio-runtime` 和 `benchmarks/runtime_drivers.py` 用于比较批量执行与
逐 action Tokio/PyO3 回调，不属于正式 API。历史测量集中在
[基准记录](./recipe_scale_benchmark.md#运行时驱动实验)。

## 唯一事实来源

Rust lowering 接受图作用域和节点成员关系，而不是同时接受 `owner`、roots 和 children：

- graph scope 使用 `Root | OwnedBy(NodeId)` 表达互斥状态
- node 使用 `Step | Composite { child_graph }` 表达互斥行为
- 每个 node 只声明自己属于哪个 `GraphId`

Rust 会校验 composite 与 child graph 的双向关系，再从 graph membership 派生 node
owner；roots 和 composite children 最终只从 owner 建立。这样层级关系不会出现三套可
互相矛盾的声明。定位后的 端口连接 也作为独立结构输入交给 Rust，再由 Rust
lower 为普通 prerequisite；Python 展示模型从 Rust 的最终 prerequisite 恢复，不保留
另一套改写规则。

节点行为使用和类型区分：

- step
- composite

prerequisite 同样使用和类型区分：

- unconditional
- conditional

运行时条件由 `ConditionId` 引用；predicate 对象保留在各宿主的侧表（Python、Node.js
或原生 Rust），core 只保存其显式结果依赖（内部 reads 索引，对应公开 API 的 inputs）。
因此 core 可以判断阻塞关系和环，但不会解释具体语言的闭包。

## 领域边界

core 不解释应用数据，也不实现特定业务的解析与持久化策略；action 与 predicate 由应用提供。
Python 通用拓扑 API 与 `rivlet-core` 的 topology 模块是两套独立实现，详见
[拓扑复杂度](./topology_complexity.md)。

## Python 边界

端口声明先由 Python adapter 验证端口对象确实属于对应子图，再把 component、port、
requirement 与 selection 映射成连续整数 ID。provider 索引、任意 provider 的唯一性、
显式 provider 选择、重复与混合 requirement 校验均在 Rust 中一次完成；Python 不保留
第二套 provider 求解规则。

当前仍留在 Python 的内容都是宿主边界，而不是另一套执行图算法：

- 静态条件叶子和运行时条件 predicate 是 Python callable
- Python action、结果对象、observe、日志路径和展示 label 是 Python 对象
- PyO3 adapter 在一次编译调用中遍历声明对象；静态 predicate 的业务求值仍调用 Python
- Python `GraphCompilation` 只恢复 action、predicate、observe、label path 与结果作用域侧表
- `ExecutionState` 保存宿主显式请求的 skip、执行结果与渲染快照

ready 队列、节点状态机、条件阻塞、层级生命周期、执行 DAG 校验、port provider
求解与 binding lowering 均只有 Rust 一份实现。继续把上述宿主对象包装成原生对象只会
增加逐对象 FFI，不属于执行图核心迁移。
