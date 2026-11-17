# 在 Python 工作流中组合独立 Rust 扩展

[English](../python-native.md) | 简体中文

> **实验性：** 混合执行、SDK、ABI 桥接与协调层整体标注，可能调整或移除；不迁入独立命名空间。
> 见[状态与移除边界](./mixed-execution.md)。

本 SDK 位于当前源码树，已发布的 0.4.0 宿主尚不包含此入口；使用前需构建对应宿主
扩展。SDK crate 尚未发布到 crates.io，示例使用显式 path 依赖。

## 导出具体节点

SDK 只提供 `rivlet_python::export_node(py, node)`：接收 Rust Step 或 Graph，返回
对应的普通 Python `rivlet.Step` 或 `rivlet.Graph`。根节点的 label、condition 和声明
身份一起导入，身份映射到宿主的身份空间；动作和谓词仍由 Rust 执行。不公开另一套
RustNode 或 NativeAction，也不保留单独的结构导出入口。
导入的 Step 进入原生 driver，不伪装成 Python callable 执行。

扩展可以在模块初始化时直接注册已创建的节点：

```rust
module.add("double", export_node(module.py(), step)?)?;
module.add("pipeline", export_node(module.py(), graph)?)?;
```

Python 直接使用 `demo_native.double` 或 `demo_native.pipeline`；不必再包装或调用
工厂。需要构造参数或独立新声明时，扩展自行提供工厂函数，不增加第二套导出协议。
Step 没有 `.interface`。

复用时显式取 Graph 自己的声明成员：

```python
left = Graph(
    nodes=pipeline.nodes,
    edges=pipeline.edges,
    interface=pipeline.interface.declaration,
    label="left",
)
right = Graph(
    nodes=pipeline.nodes,
    edges=pipeline.edges,
    interface=pipeline.interface.declaration,
    label="right",
)
```

这两个 Graph 的根身份独立，内部节点引用可复用；不自动继承原根的身份、label 或
condition。普通 Python 图如果还要复用静态规则，须显式传入其 `static` 与
`static_selections`，不能从已裁剪的执行图还原未选候选。

`pipeline.nodes` 是本层实际 Step/Graph 对象的元组；`pipeline.edges` 是本层实际
Edge 对象的元组。边的端点、条件来源与接口声明引用同一批缓存对象，不再是描述字典，
因此可以直接接线、重组。读取子 Graph 的成员看到该子图的声明，不是平铺后的执行图。

原生声明视图按需创建。不读取这些成员时，编译和运行仍走批量导入路径，不物化逐个
Python 节点/边；显式检查或复制声明才支付对应成本。检查不执行 action，也不求值
运行时谓词。

快照统一使用 `export_graph(pipeline)`，沿用[结构导出 schema](./graph_export.md)。
不增加 `describe()`，也不保留公共 GraphStructure 层。修改快照不会修改可执行声明。

若确实要保留导入根的条件，在条件存在时显式传入
`condition=native.condition.as_input()`。Rust 谓词仍由 Rust 执行，其输入仍是明确的
来源引用。单独取出一个内部节点时，它引用的同级节点也必须在新作用域中声明。导出根
不能引用未声明的外部同级节点；需要这些关系时应导出包围它们的 Graph。

Python Graph 构造器用 `interface=` 声明接口；`graph.interface` 提供绑定到该图的
端口，`graph.interface.declaration` 提供可复用定义。检查得到的原生 Step 保存私有
原生动作句柄，不是 Python callable；可接线并经混合 session 执行，不能当 Python
函数调用。

## 运行真实示例

[独立扩展源码](../../examples/python-native/src/lib.rs) 定义了类型化 Echo、Double
动作与同步谓词。`pipeline` 属性通过 `rivlet_python::export_node` 导出为真实公共
Python Graph，`double` 属性是导出的 Step。可选的 `make_graph()` 工厂只用于创建
独立的新 Graph 声明，内部使用同一个导出入口。

已安装当前宿主并准备好 Maturin 后：

```sh
just test-native-bridge
# 也可显式指定 Maturin：
python scripts/check_native_bridge.py --maturin /path/to/maturin
```

检查入口使用示例自己的 Cargo.lock 构建扩展，把 wheel 解包到临时目录，运行集成
测试与 demo，然后只清除该临时目录；不安装到日常环境。Python、pytest、Rust 与
Maturin 都是显式前置条件。

完整 Python 组合：

```python
from demo_native import pipeline
from rivlet import Edge, Graph, NodeRunContext, Option, Step, run

def show(_ctx: NodeRunContext, value: Option[int]) -> None:
    print("Python received:", value.unwrap())

prepare = Step(lambda: 21, label="prepare")
native = pipeline
display = Step(show, label="display")
workflow = Graph(
    nodes=(prepare, native, display),
    edges=(
        *Edge.results((prepare,), native.interface.input),
        *Edge.results((native.interface.output,), display),
    ),
    label="mixed",
)
run(workflow, max_workers=2)
```

输出 `Python received: 42`。Rust 判断输入为正数后才执行翻倍；负数会跳过 Double，
其消费者收到 Nothing，而不是成功的 unit。集成测试还覆盖串行、arun、借用线程池、
条件完成边、条件端口边和重复挂载。

[类型声明](../../examples/python-native/demo_native.pyi) 使用
`pipeline: Graph`、`double: Step` 和 `def make_graph() -> Graph`。
PyO3 返回 Python 对象句柄，不把 Rust
Graph 的内存布局共享给宿主二进制。

## 所有权与 ABI

- `crates/bridge` 定义版本化 C 布局头与回调表。
- `crates/python-sdk`（包名 `rivlet-python`）只提供 `export_node` 导出入口。
- 宿主批量导入元数据，把原生回调接入现有编译器与 execution session。Python 使用
  不依赖语言的 `execution::coordinator` 协调原生池、值与谓词，PyO3 对象及转换留在
  适配器；不增加第二套调度器。

元数据导入使用类型化 JSON 描述；动作、谓词与结果载荷不使用 JSON。动态库之间不
交换 Rust trait object、Arc 布局、TypeId 表示或分配器所有权。导入时校验 ABI 版本、
头与回调表尺寸、结构引用和端口归属。回调 panic 转为明确错误，但这不是原生代码沙盒。

图和值的分配及释放回调始终属于创建库。图句柄与保留结果持有创建方 lease，直到最后
一个读取者释放。协议要求同进程、同架构和保持加载的 Python 扩展库，不是 IPC 协议，
也不提供显式卸载动态库 API。


当前实验描述协议为 ABI v3：原始声明端点与 lowering 后的调度索引分开传输。旧 ABI v2
扩展必须按匹配的 SDK 重新构建；版本不匹配会明确报错，不推断、不降级。

## 值与条件

内置转换支持 unit、bool、整型、浮点、UTF-8 字符串和字节。跨库数字使用小端二进制，
字符串使用 UTF-8，字节保留原内容；只在实际读取方需要时转换。跳过保持缺失，成功的
unit 保持存在。

同一扩展 provider 内的自定义 `Send + Sync + 'static` 载荷不要求 Clone 或序列化。
示例把不可 Clone 的同一载荷传给两个参数，在 Rust 内检查 Arc::ptr_eq。第二份动态库
映像是不同 provider，Rust 类型名相同不代表可以互传。

SDK 当前没有跨 provider 或 Python 自定义转换器注册 API；这些使用会明确报错，
不猜测表示。内置值可以跨 Python 和独立 Rust 扩展，任意 Python 对象仍由 Python 持有。

Rust 同步谓词由混合派发直接执行，求值期间释放 GIL。节点、子图、完成边和端口边
各自保留挂载输入与决策。谓词应短小，因为它运行在 driver 而非动作 worker。
Python 谓词仍可异步。

本 SDK 不增加 Rust 异步谓词、运行时生成 Rust 图或 Node.js 前端的原生动作/谓词
注册，也不构成性能承诺；实际工作流需要单独测量。
