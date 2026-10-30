# Rivlet

[English](https://github.com/ririv/rivlet/blob/main/README.md) | 简体中文

Rivlet 是一个用于组合和执行任务图的 Python 库，执行核心由 Rust 实现。
把任务写成 Python 函数，显式连接依赖，再把完整工作流作为子图复用。

适合需要清晰依赖和可控并发的构建流水线、自动化、数据处理与校验工作流。
它运行在你的应用进程内，不是分布式工作流服务。

## 可以做什么

- **组合步骤与子图**：用 `>>` 表达先后依赖，用 `|` 组合独立分支，复用时保留子图边界。
- **通过出入口同步就绪状态**：消费者可以提前准备，只在真正需要提供方产出的位置等待，
  不必等提供方的整个工作流结束。
- **显式表达数据流**：用结果边把步骤返回值绑定到 action 或条件函数的参数；
  只需执行顺序的依赖不用传递结果。
- **控制执行方式**：支持同步与异步 Python 任务，显式设置并发度；运行时创建的子图
  也共享同一份动作并发预算。
- **查看执行过程**：渲染图、查询结果、订阅强类型执行事件。步骤失败只阻断相关下游，
  无关分支继续运行，整次执行最终仍会报告失败。

## 安装

需要 Python 3.13 或更高版本：

```bash
pip install rivlet
```

CPython 3.13–3.15 在 Linux x86_64（glibc 2.28+）和 macOS arm64（macOS 11+）上
提供预编译 wheel，安装这些 wheel 不需要 Rust 工具链。其它平台需要从源码构建，见
[交付范围](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/releasing.md)。

## 组合步骤与可复用子图

```python
from rivlet import Graph, GraphExecution, Step, ThreadGraphRunOptions


def checks() -> Graph:
    validate = Step(lambda: print("validate"), label="validate")
    test = Step(lambda: print("test"), label="test")
    return (validate | test).graph(label="checks")


prepare = Step(lambda: print("prepare"), label="prepare")
finish = Step(lambda: print("finish"), label="finish")

graph = (prepare >> checks() >> finish).graph(label="example")
GraphExecution(graph=graph).run(ThreadGraphRunOptions(workers=2))
```

`prepare` 完成后，`checks` 子图中的 `validate` 和 `test` 可以并发执行。
`finish` 等待两者成功完成；两者的打印顺序不保证。

`checks()` 返回普通的 `Graph`，可以放进其它工作流。嵌套后保留其边界与内部依赖。
`>>` 等待左侧整个步骤或子图完成；`|` 不在分支之间添加依赖。实际并发度由执行选项
控制，不是写了 `|` 就一定并行。

声明与运行分开：`Graph` 描述工作流，`GraphExecution(graph)` 持有一次执行。
同一声明再次运行时，新建一个执行对象即可。也可以不用 DSL，直接声明相同接线，见
[核心模型](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/core_model.md)。

## 用出入口连接子图

有时下游只需要上游的某项中间产出，不需要等待整个上游子图结束。
能力（`Capability`）为这项就绪契约命名：出口指向让它就绪的步骤，入口指向消费者
开始需要它的步骤。

下面是连接两个原本独立子图的完整示例：

```python
from rivlet import (
    Capability,
    CapabilityBinding,
    CapabilityInterface,
    Graph,
    GraphExecution,
    Step,
    ThreadGraphRunOptions,
)

data_ready = Capability("validated-data")

fetch = Step(lambda: print("fetch data"), label="fetch")
validate = Step(lambda: print("validate data"), label="validate")
archive = Step(lambda: print("archive data"), label="archive")
producer_graph = (fetch >> validate >> archive).graph(
    label="producer",
    capabilities=CapabilityInterface(outputs={data_ready: validate}),
)

prepare = Step(lambda: print("prepare consumer"), label="prepare")
process = Step(lambda: print("process data"), label="process")
report = Step(lambda: print("report results"), label="report")
consumer_graph = (prepare >> process >> report).graph(
    label="consumer",
    capabilities=CapabilityInterface(inputs={data_ready: process}),
)

producer = producer_graph.step()
consumer = consumer_graph.step()
graph = Graph(
    label="pipeline",
    steps=(producer, consumer),
    edges=(
        CapabilityBinding(source=producer, target=consumer, capability=data_ready),
    ),
)
GraphExecution(graph).run(ThreadGraphRunOptions(workers=3))
```

```text
producer: fetch → validate [output] → archive
                     │ validated-data
                     ▼
consumer: prepare → process [input] → report
```

消费者的 `prepare` 可以立即开始。`validate` 成功且消费者自己的准备也完成后，
`process` 就可以运行，不需要等待 `archive`。整次执行仍会等待两个子图结束。

端口名称相同**不会自动建立依赖**，示例用 `CapabilityBinding` 显式连接提供方与消费者。
端口负责就绪同步，不会传递 Python 返回值，也不会检查外部数据；传参使用结果边。
声明出口时，必须保证后续步骤不会再使消费者读取的内容失效。

这与 `producer_graph >> consumer_graph` 不同：后者会等整个提供方完成后才开始
消费者的任何步骤。多个提供方的选择、多个入口和失败行为见
[能力依赖与子图端口](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/capabilities.md)。

### 包装子图时转发接口

沿用上例的 `producer_graph`：

```python
setup = Step(lambda: print("set up producer"), label="setup")
wrapped = (setup >> producer_graph).graph(
    label="wrapped producer",
    capabilities=producer_graph,
)
```

`capabilities=producer_graph` 显式把内部子图的出入口公开到外层；不写时，外层不公开
任何端口。需要时也可以通过 `CapabilityInterface` 只转发一个方向，或声明新接口。

## 更多用法

- [结果输入与动作上下文](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/core_model.md#结果与运行上下文)
- [条件步骤](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/conditional_steps.md)与[条件边](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/conditional_edges.md)
- [异步执行、取消与失败传播](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/execution.md)
- [强类型失败事件](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/failure_events.md)
- [完整文档](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/index.md)

参与开发请参阅[本地开发与检查](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/development.md)、
[内部架构](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/rust-architecture.md)和
[发行流程](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/releasing.md)。

## 许可证

本项目采用 [MIT 许可证](https://github.com/ririv/rivlet/blob/main/LICENSE)。
