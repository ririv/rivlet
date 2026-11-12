# 结构化图导出

[English](../graph_export.md) | 简体中文

`export_graph()` 直接读取实际的 `Graph` 或 `GraphCompilation`，返回独立的纯 JSON 数据。
它不反向解析 Mermaid 或文本、不根据标签猜依赖，也不执行 Action。公开 schema 是
[`GraphDocument`](../../src/rivlet/structure/schema.py)：按 `view` 区分的 TypedDict 联合类型，
没有 HTTP、前端样式或 Pydantic 依赖，不是另一套可执行图模型或反序列化执行入口。

## 声明与编译结果

```python
import json
from rivlet import Case, GraphExecution, Step, export_graph, static

graph = static.select(cases=(
    Case(True, Step(lambda: "private result", label="keep")),
    Case(False, Step(lambda: None, label="drop")),
)).graph(label="choose")

declared = export_graph(graph)  # 不选择分支，不调用静态求值器
execution = GraphExecution(graph)
prepared = execution.compile()  # 在这里求静态条件，成功后固定本次结构
compiled = export_graph(prepared)  # 不重新编译、不重复求值
assert execution.compile() is prepared
json.dumps({"declaration": declared, "compiled": compiled})
execution.run()  # 使用刚才导出的同一份编译结果
assert execution.compilation is prepared
```

上例的关键字段如下；为便于对比，省略了公共节点字段及图作用域表，实际返回值总是包含
对应 TypedDict 分支的全部字段：

```json
{
  "declaration": {
    "schema_version": 2,
    "view": "declaration",
    "nodes": [{"id": "0", "label": "keep"}, {"id": "1", "label": "drop"}],
    "selections": [{"id": "graph:root/selection:0", "cases": [
      {"node": "0", "condition": true}, {"node": "1", "condition": false}
    ]}]
  },
  "compiled": {
    "schema_version": 2,
    "view": "compiled",
    "nodes": [{"id": "0", "label": "keep"}],
    "edges": [],
    "dependencies": [],
    "excluded_nodes": ["1"],
    "excluded_edges": []
  }
}
```

声明导出保留全部候选，包括尚未经过编译验证的零命中或多命中选择。它不调用静态求值器、
运行期条件或 Action。不透明静态规则只记录目标引用与条件位置 ID，不导出任意 payload、
evaluator、描述字符串，也不猜测求值结果。`Case` 中已经明确给出的 bool 属于声明数据，
正常保留。

编译导出只保留通过静态裁剪的节点和边。`excluded_nodes`、`excluded_edges` 引用对应声明
中的位置身份，不是运行期 skipped 状态。运行期条件此时尚未执行，因此编译图不是执行轨迹。
编译条件表提供真实 session 内的 `index`、ID 和有序输入节点 ID，节点条件与条件边
引用该表。同一个函数或条件元组复用在多个位置时，可以产生多个编译条件实例。

## 身份与组合边界

- 节点 `id` 等于 `str(StepPath)`，例如 `0`、`2.1`，表示挂载位置，不是标签、函数身份、
  进程对象地址或永久业务身份。
- 同一子图挂载两次会得到 `0.0`、`1.0` 等不同 ID；导出和运行事件使用同一个 StepPath 命名空间。
- 节点 `step_id` 是声明的进程内整数身份；重复挂载共享它，同一声明重新编译也保持不变。
  它不是跨进程持久身份，也不作为缓存键。
- 挂载结构不变时，改标签不改变 ID；调整节点顺序或组合结构可能改变 ID。必须结合当次冻结
  的图文档或 run 身份使用，不能拿另一版源码生成的图来解释旧事件。
- `label_path` 表示挂载标签路径。根作用域 ID 为 `graph:root`，子作用域为
  `graph:<owner StepPath>`。作用域的 `owner`、本地 `nodes` 和组合节点的 `child_graph`
  共同保留子图边界，不伪造 no-op Action。
- `ports` 明确记录端口名称及 input/output 对应的内部节点；显式转发的端口记录在公开它的
  那个作用域，不自动展开或转发。

图名、节点标签和端口名称本来就是公开展示／契约数据，会正常导出。宿主不要把秘密放进这些
名称。未命名节点使用位置 ID；导出不会调用 callable 的 `repr()` 猜名字。

## 边的语义

两种视图保留声明边 ID，明确区分：

| `kind` | 含义 |
| --- | --- |
| `order` | 只等待完成、不传返回值的顺序依赖 |
| `result` | 按 `argument_index` 绑定给 action 的结果参数；条件来源记录在条件表的 `inputs` |
| `conditional` | 由对应运行期条件决定是否生效的依赖 |
| `port` | 两个挂载组件的接线，分别记录 `source_port`、`target_port`、可选条件 ID 及实际 `provider_node`／`consumer_nodes` |

编译文档中的 `dependencies` 单独描述 Rust 编译器实际采用的前置关系，分为 `required`
与 `conditional`。端口边在 `edges` 中可以是组件 `0 → 1`，在 `dependencies` 中则是
端点 `0.0 → 1.1`，不会变成“整个消费者等待整个提供者”。不会从 `after` 反猜来源，
也不会把结果、条件、端口边全部冒充顺序边。

解释调度时还需要保留条件的 `inputs` 和作用域所有权，不能仅用前置列表取代这些契约。
静态边的裁剪决定由编译器在首次求值时记录，导出直接读取，不再求一遍。

## 冻结历史与运行状态分开

把返回的文档随实际使用它的请求／历史记录保存。例如，先序列化
`export_graph(execution.compile())`，再调用 `run()`。返回的字典和列表是独立副本，修改它们
不会影响 Graph 或下次导出；保存 JSON 才是冻结该次历史的具体值。

`schema_version=2` 只表示文档格式，不是 Rivlet 版本、工作流修订、run ID 或 fingerprint。
第 2 版使用 `port` 边类型，以 `source_port`、`target_port` 分别记录两端名称；
`condition` 为条件 ID，无条件时为 null。图接口自身的端口声明仍用单个 `port` 名称。
第 1 版以 `capability` 表示边类型和双方共同名称。
消费者必须按 schema 版本识别格式，不能把旧记录当成新格式读取。
宿主在外层单独记录自己的运行／修订身份。不要用今天的 Graph 重画旧历史，不给旧历史补上
当时不存在的节点，更不能把无法证明执行过的工作标成已执行。

运行事件和快照仍然独立，通过当次冻结图中的节点 ID 关联。图文档不包含返回值、共享状态、
异常正文、日志路径、命令行、环境变量或函数对象。Rust 原生子图也只暴露标签和拓扑，
不暴露进程执行详情。

`GraphExecution` 的声明与宿主引用绑定后不可替换。首次成功的 `compile()`、`run()` 或
`arun()` 固定根编译结果，后续复用；编译异常不提交半成品，可以重试。这只是本次准备结果
复用，不是 Action／业务结果缓存，也不让单次执行状态变成可重复运行的状态。重新求静态条件
需要新建 GraphExecution。独立工具 `compile_graph()` 仍每次独立编译，不会准备另一个 execution。

## 范围与交付

导出只描述所给作用域，不能预知未来工作。Action 返回的动态子图必须等它实际产生并编译后，
再按已有父 StepPath 前缀单独导出；根文档不会被悄悄重写成“预知全部动态工作”的历史。
这类挂载导出的 `parent` 标识文档外的拥有者，`root` 为 `graph:<parent StepPath>`；拥有者本身
不在该子文档的节点表中。独立根图的 `parent=null`、`root="graph:root"`。这样单独导出的
子文档在关联时，作用域与边 ID 也保留挂载命名空间，不只节点 ID 带前缀。

手工拼装而没有编译来源的 GraphCompilation 会明确拒绝导出，不重新编译或猜边类型。
请通过 compile_graph 或 GraphExecution.compile 获取编译器产物。

这是新源码 API，PyPI 的 0.2.0.post1 和原始 0.3.0 准备提交都不包含它。消费方必须显式使用
经过验收、包含本次改动的构建，安装升级属于另外一次部署操作。导出 JSON 不意味着增加 JS
绑定、跨语言执行器、服务、旧格式兼容读取或自动发布。Python 投影没有增加第三方依赖，
原有 Python 适配层仅补充编译来源和原生图的安全结构读取。

导出开销与挂载节点、边、端口、条件及输入引用、路径／输出总长度成正比，不执行 Action I/O，
不改变调度。编译来源侧表不复制另一套拓扑，只记录原有编译过程已经作出的决定。
