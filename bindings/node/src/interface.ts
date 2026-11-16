import { Graph, resultValueType, type Node } from "./model.js";

export type PortName = string | null;
export type InputEndpoint = Node | InputPort;
export type OutputEndpoint = Node | OutputPort;
type InputGroup = InputEndpoint | readonly [InputEndpoint, ...InputEndpoint[]];

function isMap<K, V>(value: unknown): value is ReadonlyMap<K, V> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    "entries" in value &&
    typeof value.entries === "function" &&
    "get" in value
  );
}
function isGroup(
  value: InputGroup,
): value is readonly [InputEndpoint, ...InputEndpoint[]] {
  return Array.isArray(value);
}
export interface InterfaceOptions {
  readonly inputs?: InputGroup | ReadonlyMap<PortName, InputGroup>;
  readonly outputs?: OutputEndpoint | ReadonlyMap<PortName, OutputEndpoint>;
}

function checkName(name: PortName): void {
  if (name !== null && (typeof name !== "string" || name.length === 0)) {
    throw new Error("port names must be nonempty strings or null");
  }
}

/** A direction-specific reference belonging to one actual Graph, not its structure. */
export class InputPort {
  readonly direction = "input";
  constructor(
    readonly owner: Graph,
    readonly name: PortName,
  ) {
    if (!(owner instanceof Graph))
      throw new Error("input port owner must be a Graph");
    checkName(name);
    Object.freeze(this);
  }
}
export class OutputPort<T = unknown> {
  readonly direction = "output";
  declare readonly [resultValueType]: T;
  constructor(
    readonly owner: Graph,
    readonly name: PortName,
  ) {
    if (!(owner instanceof Graph))
      throw new Error("output port owner must be a Graph");
    checkName(name);
    Object.freeze(this);
  }
}

/** Pure interface declarations, reusable together with a GraphStructure. */
export class GraphInterface {
  readonly #inputs: ReadonlyMap<PortName, readonly InputEndpoint[]>;
  readonly #outputs: ReadonlyMap<PortName, OutputEndpoint>;
  constructor(options: InterfaceOptions = {}) {
    const inputs: ReadonlyMap<PortName, InputGroup> =
      options.inputs === undefined
        ? new Map()
        : isMap<PortName, InputGroup>(options.inputs)
          ? options.inputs
          : new Map([[null, options.inputs]]);
    this.#inputs = new Map(
      [...inputs].map(([name, group]) => {
        checkName(name);
        const endpoints = isGroup(group) ? group : [group];
        if (endpoints.length === 0)
          throw new Error("an input port requires at least one endpoint");
        return [name, Object.freeze([...endpoints])];
      }),
    );
    const outputs: ReadonlyMap<PortName, OutputEndpoint> =
      options.outputs === undefined
        ? new Map()
        : isMap<PortName, OutputEndpoint>(options.outputs)
          ? options.outputs
          : new Map([[null, options.outputs]]);
    this.#outputs = new Map(outputs);
    for (const name of this.#outputs.keys()) checkName(name);
    Object.freeze(this);
  }
  input(name: PortName): readonly InputEndpoint[] {
    const endpoints = this.#inputs.get(name);
    if (endpoints === undefined)
      throw new Error(`input port is not declared: ${String(name)}`);
    return endpoints;
  }
  output(name: PortName): OutputEndpoint {
    const endpoint = this.#outputs.get(name);
    if (endpoint === undefined)
      throw new Error(`output port is not declared: ${String(name)}`);
    return endpoint;
  }
}

export class GraphInterfaceView {
  constructor(readonly owner: Graph) {
    Object.freeze(this);
  }
  input(name: PortName = null): InputPort {
    this.owner.structure.interface.input(name);
    return new InputPort(this.owner, name);
  }
  output<T = unknown>(name: PortName = null): OutputPort<T> {
    this.owner.structure.interface.output(name);
    return new OutputPort<T>(this.owner, name);
  }
}
