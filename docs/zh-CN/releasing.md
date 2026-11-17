# Rivlet 发行说明

[English](../releasing.md) | 简体中文

发行包、Python 导入包和 Rust 核心 crate 统一叫 `rivlet`，公开仓库为
<https://github.com/ririv/rivlet>。不保留旧的 `rivetoo_graph` 导入别名。

## 交付范围

发行矩阵覆盖 CPython 3.12、3.13、3.14、3.15，以及 Linux x86_64（glibc 2.28 起）、
macOS arm64（macOS 11 起）与 Windows x86_64（`win_amd64`，MSVC）。同时提供源码包。
其它平台需要兼容的 Rust 工具链自行编译源码，不能据此声称这些平台已经通过 wheel 验收。
此矩阵不包含 Windows ARM64 或自由线程 Python。

原生扩展是必需依赖，没有纯 Python 兜底实现。项目尚处于 1.0 之前；首次公开发布
不等于承诺 API 已稳定。

## 版本规则

在 0.x 阶段，新增公开图端口或破坏公开 API 的变更提升次版本；兼容修复提升修订版本。
已经发布的产物不能覆盖。

不改变库本身的文档或发行元数据更新，使用 PEP 440 后发行版本，例如
`0.2.0.post1`。Python 发行号与标签 `v0.2.0.post1` 带后缀，Rust crate 保持基础版本
`0.2.0`。这是一次新发行，不覆盖已有的 0.2.0。真正的软件修复应提升修订版本或
次版本，不使用后发行号。执行 `python scripts/check_release.py` 检查当前 manifest；
加上 `--tag v0.4.0` 可验证当前准备创建的标签。脚本支持正式版及其规范的 `.postN`
形式，不接受其它未支持的版本写法。

### 尚未发布的 API 迁移

Graph 直接接收节点、边、接口与静态声明，删除独立的 GraphStructure，不保留兼容别名。
这是公开 API 变更，需要下一个次版本发布；不修改现有发行文件。

### 0.4.0

本版包含公开 API 变更，不能作为 0.3.0 的无修改替换。

最低支持 Python 3.12。Rust worker 默认容量由 Rust 托管 session 决定，
不再依赖 Python 3.13 的 CPU 查询 API。

- Step 是动作节点，Graph 是子图节点，二者实现共同的 Node 协议；GraphStructure
  保存可复用拓扑。Execution 可以直接执行这两种根节点，不再把图包装成动作步骤。
- 图实例拥有明确归属的出入口引用，支持就绪边与结果边。字符串具名端点可以与
  `None` 匿名端点共存；`.interface.input` / `.output` 只选择显式声明的匿名端点，
  不自动选择唯一具名端点。见[图接口](./interfaces.md)。
- 通过 `Edge.results(...)` 或 wiring 中的 `Results(...)` 声明位置结果传递；
  运行时条件使用 `condition=(predicate, producer, ...)`。动作上下文不再提供任意
  跨节点结果查询，结果参数仍是 Option。见[核心模型](./core_model.md)。
- `run` / `arun` 托管执行资源，低阶 `Execution.run` / `arun` 借用线程池。
  `max_workers` 限制阻塞 Python worker；可选的执行级 `max_concurrency` 按真实子图
  层级约束后代动作，同时覆盖同步、异步和 Rust 工作。见[执行模型](./execution.md)。
- Rust 图声明、强类型原生结果传递和 Rust 执行与 Python 共用同一个图内核，
  Python 值转换在适配边界显式处理。见[Rust 图](./rust-graphs.md)与
  [Rust 结果](./rust-results.md)。

`@step` 仍声明实际 Step；本版没有函数调用式构图前端。构图继续使用构造器、
wiring/DSL 和返回 Graph 的普通函数。已移除的无参 `@graph` 不保留别名。
本版不包含 JavaScript、Swift 或 Java 绑定。

### 0.3.0

0.3.0 新增图声明内的静态选择：`static.select()` 与 `Case(condition, component)`。
所有候选仍保留在声明中供检查；编译要求每个活跃选择恰好一个条件成立，不按分支顺序
决定优先级，没有隐式兜底，也不增加选择步骤。组合与端口边界见
[静态选择](./static_selection.md)。原有绑定 evaluator 的 `StaticConditions` 规则继续保留。

本版同时增加 `export_graph()` 版本化 JSON 结构导出，区分声明和编译视图，保留边类型来源、
挂载身份、端口及静态排除项。Execution 首次成功编译后由 compile/run/arun
复用，声明与宿主绑定不可替换；需要重新求静态条件时创建新实例。见[结构导出](./graph_export.md)。

本版不包含 JavaScript、Swift、Java 绑定、跨语言 Action 编排或浏览器支持。

0.2.0 新增强类型的直接失败与阻断来源。步骤事件消费方把 `event.phase` 改为
`event.state`，`StepExecutionPhase` 改为 `StepExecutionState`。构造事件使用
`state_detail=`，只读的 `state` 从中推导，不保留旧名称别名。完整模型与迁移边界见
[失败事件](./failure_events.md)。这次发行没有增加检查点恢复、自动重试或事件序号。

## 验证与发布

配置矩阵不代表产物已经通过验收；必须构建成功，并在目标操作系统完成安装后测试，才能发布。
交叉编译的 wheel 同样必须经过目标系统的原生验证，交叉编译成功不能代替运行测试。
本地构建和 CI 共用的验收入口见 [Windows 环境](./development.md#windows-环境)；
本地验证和 token 上传不需要 Actions。

`.github/workflows/publish.yml` 提供两个明确入口：

- 手动触发只生成并验证候选产物，不上传 PyPI。
- 推送 `v<version>` 标签执行相同验证，全部通过后才上传。标签必须匹配 Python
  项目版本，Cargo workspace 必须匹配去掉 `.postN` 后的基础版本；任何验证失败
  都会阻止整个发行上传。

每个平台先安装 wheel，再以 Python 隔离模式运行测试，避免误用 checkout 内的源码。
源码任务检查 Python 和 Rust，生成 sdist，再从 sdist 构建并安装 wheel，运行测试。
所有平台任务下载并使用这同一份 sdist，不再各自从 checkout 重建源码。
Twine 检查发行元数据。历史基准数据不作为本轮发行的性能保证。

## 本地与 CI 共用入口

`justfile` 只是可选的命令入口，实际流程在 `scripts/build_dist.py`、
`scripts/verify_wheel.py` 和 `scripts/publish_dist.py`。Actions 直接调用这些相同脚本，
不另写一套构建实现。执行 `python scripts/build_dist.py settings` 可查看发行使用的
Rust/Maturin 版本和 Python 矩阵。宿主工具与独立验收环境由调用者显式准备，脚本不在
宿主自动安装工具；只有显式容器入口会在一次性容器内准备工具链。

调用 Just 前激活选定的 Python 环境。其任务使用 Python，不隐式要求 Windows 提供
Unix shell。仓库根目录的使用示例：

```sh
just sdist dist/source
just verify-source dist/source/rivlet-0.4.0.tar.gz dist/receipts

# Linux Docker 宿主：官方 manylinux 环境，不使用 Zig。
just wheel-linux dist/source/rivlet-0.4.0.tar.gz dist/linux-313 3.13

# 无容器 Linux：显式准备 Zig 与对应 CPython 解释器。
just wheel-linux-zig dist/source/rivlet-0.4.0.tar.gz dist/linux-zig-313 python3.13

# macOS 原生；Windows 构建宿主改用 x86_64-pc-windows-msvc。
just wheel-native dist/source/rivlet-0.4.0.tar.gz dist/macos-313 aarch64-apple-darwin python3.13

just verify-wheel path/to/rivlet.whl dist/receipts
just check-dist path/to/distributions dist/receipts
just publish path/to/distributions dist/receipts
```

示例版本号仅用于展示，实际使用当前 manifest 生成的文件名。修改工作流不代表可以
覆盖已发布版本。输出目录必须为空，已有构建和验收记录均不覆盖。每个 wheel 使用
匹配的目标解释器测试；交叉编译后把 wheel 送到目标系统验收，不能在交叉编译宿主上
生成成功记录。

Linux 两个入口显式二选一，不自动降级或切换：

- `wheel-linux` 用 Docker 启动官方 manylinux 2.28 容器；Actions 使用此入口，不需要 Zig。
- `wheel-linux-zig` 在 Linux 直接向 Maturin 传入 `--zig --manylinux 2_28`。
  调用者自行准备 Zig、cargo-zigbuild；Maturin 审计实际产物，不把普通宿主产物直接改标签。

直接调用脚本时，`build_dist.py native`、`manylinux` 和 `linux-zig` 接收 sdist、输出目录，
以及 `--interpreter` 后的一组解释器路径；native 还必须提供 `--target`。
`container` 则接收 `--python-version`。命令里不写死 SSH 主机、sudo 策略、SDK 位置或用户
目录。交叉编译环境仍显式配置，不自动准备第二台机器或安装 Windows SDK。

验收仅在元数据检查、隔离安装和完整测试通过后写 JSON 记录，保存文件 SHA-256、版本和
验收解释器／平台，测试结束再核对摘要。`verify-source` 必须先从 sdist 构建并测试 wheel，
才记录源码包通过。本地上传和 CI 暂存均拒绝缺少记录或字节已变化的产物。
本地上传先把已检查产物复制到私有临时目录再调用 Twine；CI 在 Trusted Publishing 前
暂存同样经过检查的文件。记录只是工作流证据，**不是签名来源证明**，不能防止发布者
主动伪造记录。

构建和验收不上传。`just publish` 才是显式的 token 上传入口；Twine 配置放在仓库外，
例如 `~/.pypirc`。使用此入口时清除 `TWINE_REPOSITORY_URL`，避免绕开具名 `pypi` 仓库
的凭据配置。不把凭据放进 Just、记录、产物或脚本。Actions 保留独立的 OIDC 发布方式。

CI 在发布前调用同一个门禁：

```sh
python scripts/publish_dist.py stage dist dist/receipts upload
```

暂存目录只含已验收的发行包，不包含验收记录本身。

`scripts/verify_wheel.py` 接收 `--wheel FILE`，或 `--dist DIRECTORY` 指定只含一个 wheel
的目录，用当前 Python 解释器完成安装后
检查。每个平台／Python 组合使用独立虚拟环境和输出目录。脚本不展开 shell 通配符，
不隐式安装依赖，也不发布；运行前显式安装 `pytest` 和 `twine`。
通过 `--receipts DIRECTORY` 保存成功记录；验证源码包时，`--source-root` 显式指定解包后的测试来源。

PyPI 使用 Trusted Publishing，绑定 `ririv/rivlet` 仓库、`publish.yml` 工作流和
`pypi` GitHub environment。只有发布任务取得 `id-token: write`，构建任务只有仓库
读取权限，不保存长期 PyPI token。

Actions 不可用时，本地发布也必须先完成拟上传产物的构建与安装后检查。
之后可以向同一版本追加已验收的 wheel，但不能覆盖已经发布的同名文件。
记录源码 commit、解释器／工具链版本、产物摘要和安装后测试结果。项目级 PyPI token 必须
保存在仓库外并限制访问，不能放入发行说明或产物；本地 token 认证不等同于 GitHub OIDC
来源证明。

首次上传前，PyPI 账户所有者需要配置项目名为 `rivlet` 的 pending publisher。
配置本身不会占用名称。已经上传的版本不能覆盖；修复必须发布新版本。
