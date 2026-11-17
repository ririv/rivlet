import {
  resultValueType,
  resultInputTypes,
  type AnyGraph,
  type Node,
  type StepNode,
} from "./model.js";

export type PortName = string | null;
export type InputEndpoint = Node | InputPort<never>;
export type OutputEndpoint = Node | OutputPort;
type InputGroup = InputEndpoint | readonly [InputEndpoint, ...InputEndpoint[]];
type Entries<T> = readonly (readonly [PortName, T])[];
export type InputDeclaration =
  InputGroup | ReadonlyMap<PortName, InputGroup> | Entries<InputGroup>;
export type OutputDeclaration =
  | OutputEndpoint
  | ReadonlyMap<PortName, OutputEndpoint>
  | Entries<OutputEndpoint>;
export type InterfaceOptions<
  I extends InputDeclaration | undefined = InputDeclaration | undefined,
  O extends OutputDeclaration | undefined = OutputDeclaration | undefined,
> = { readonly inputs?: I; readonly outputs?: O } & (undefined extends I
  ? {}
  : { readonly inputs: I }) &
  (undefined extends O ? {} : { readonly outputs: O });
export const interfaceTypes = Symbol("interfaceTypes");
export const createInterfaceView = Symbol("createInterfaceView");
export type AnyGraphInterface = GraphInterface<
  InputDeclaration | undefined,
  OutputDeclaration | undefined
>;

type Names<D> =
  D extends ReadonlyMap<infer K, unknown>
    ? Extract<K, PortName>
    : D extends Entries<unknown>
      ? D[number][0]
      : D extends undefined
        ? never
        : null;
type Lookup<D, N> =
  D extends ReadonlyMap<infer K, infer V>
    ? N extends K
      ? V
      : never
    : D extends Entries<unknown>
      ? Extract<D[number], readonly [N, unknown]>[1]
      : N extends null
        ? D
        : never;
type Intersection<U> = (
  U extends unknown ? (input: U) => void : never
) extends (input: infer I) => void
  ? I
  : never;
type EndpointInputs<E> =
  E extends StepNode<unknown, infer I>
    ? I
    : E extends InputPort<infer I>
      ? I
      : never;
type GroupInputs<G> = G extends readonly InputEndpoint[]
  ? Intersection<EndpointInputs<G[number]>>
  : EndpointInputs<G>;
type UnsupportedEndpoint<E> = E extends StepNode
  ? false
  : E extends InputPort<infer I>
    ? [I] extends [never]
      ? true
      : false
    : true;
type UnsupportedGroup<G> = G extends readonly InputEndpoint[]
  ? UnsupportedEndpoint<G[number]>
  : UnsupportedEndpoint<G>;
type CheckedInputs<G> =
  true extends UnsupportedGroup<G>
    ? never
    : Intersection<GroupInputs<G>> extends infer I extends readonly unknown[]
      ? I
      : never;
type OutputValue<E> =
  E extends StepNode<infer T>
    ? T
    : E extends OutputPort<infer T>
      ? T
      : E extends AnyGraph
        ? undefined
        : never;
type InputsOf<I extends AnyGraphInterface> = I[typeof interfaceTypes]["inputs"];
type OutputsOf<I extends AnyGraphInterface> =
  I[typeof interfaceTypes]["outputs"];

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
function checkName(name: PortName): void {
  if (name !== null && (typeof name !== "string" || name.length === 0))
    throw new Error("port names must be nonempty strings or null");
}
const createInput = Symbol("createInput");
const createOutput = Symbol("createOutput");

/** References derive their types only from their owning interface declaration. */
export class InputPort<Inputs extends readonly unknown[] = readonly unknown[]> {
  readonly direction = "input";
  declare readonly [resultInputTypes]: (values: Inputs) => void;
  private constructor(
    readonly owner: AnyGraph,
    readonly name: PortName,
    key: symbol,
  ) {
    if (key !== createInput)
      throw new Error("obtain input ports from a graph interface");
    Object.freeze(this);
  }
  static [createInput]<I extends readonly unknown[]>(
    owner: AnyGraph,
    name: PortName,
  ): InputPort<I> {
    return new InputPort(owner, name, createInput);
  }
}
export class OutputPort<T = unknown> {
  readonly direction = "output";
  declare readonly [resultValueType]: T;
  private constructor(
    readonly owner: AnyGraph,
    readonly name: PortName,
    key: symbol,
  ) {
    if (key !== createOutput)
      throw new Error("obtain output ports from a graph interface");
    Object.freeze(this);
  }
  static [createOutput]<T>(owner: AnyGraph, name: PortName): OutputPort<T> {
    return new OutputPort(owner, name, createOutput);
  }
}

/** Reusable declarations retaining endpoint signatures and literal port names. */
export class GraphInterface<
  const I extends InputDeclaration | undefined = undefined,
  const O extends OutputDeclaration | undefined = undefined,
> {
  declare readonly [interfaceTypes]: {
    readonly inputs: I;
    readonly outputs: O;
  };
  readonly #inputs: ReadonlyMap<PortName, readonly InputEndpoint[]>;
  readonly #outputs: ReadonlyMap<PortName, OutputEndpoint>;
  constructor(options: InterfaceOptions<I, O>);
  constructor(
    ...args: I extends undefined
      ? O extends undefined
        ? []
        : [unavailable: never]
      : [unavailable: never]
  );
  constructor(options: { readonly inputs?: I; readonly outputs?: O } = {}) {
    const input = options?.inputs;
    const inputEntries: Iterable<readonly [PortName, InputGroup]> =
      input === undefined
        ? []
        : isMap<PortName, InputGroup>(input)
          ? input
          : Array.isArray(input) &&
              (input.length === 0 || Array.isArray(input[0]))
            ? (input as Entries<InputGroup>)
            : [[null, input as InputGroup]];
    const inputs = new Map<PortName, readonly InputEndpoint[]>();
    for (const [name, group] of inputEntries) {
      checkName(name);
      if (inputs.has(name)) throw new Error("duplicate input port");
      const endpoints = Array.isArray(group) ? group : [group];
      if (endpoints.length === 0)
        throw new Error("an input port requires at least one endpoint");
      inputs.set(
        name,
        Object.freeze([...endpoints]) as readonly InputEndpoint[],
      );
    }
    const output = options?.outputs;
    const outputEntries: Iterable<readonly [PortName, OutputEndpoint]> =
      output === undefined
        ? []
        : isMap<PortName, OutputEndpoint>(output)
          ? output
          : Array.isArray(output)
            ? (output as Entries<OutputEndpoint>)
            : [[null, output as OutputEndpoint]];
    const outputs = new Map<PortName, OutputEndpoint>();
    for (const [name, endpoint] of outputEntries) {
      checkName(name);
      if (outputs.has(name)) throw new Error("duplicate output port");
      outputs.set(name, endpoint);
    }
    this.#inputs = inputs;
    this.#outputs = outputs;
    Object.freeze(this);
  }
  input(name: PortName): readonly InputEndpoint[] {
    const endpoints = this.#inputs.get(name);
    if (endpoints === undefined)
      throw new Error(`input port is not declared: ${String(name)}`);
    return endpoints;
  }
  /** Detached entries for read-only structural inspection. */
  inputEntries(): readonly (readonly [PortName, readonly InputEndpoint[]])[] {
    return Object.freeze(
      [...this.#inputs].map((entry) => Object.freeze(entry)),
    );
  }
  outputEntries(): readonly (readonly [PortName, OutputEndpoint])[] {
    return Object.freeze(
      [...this.#outputs].map((entry) => Object.freeze(entry)),
    );
  }
  output(name: PortName): OutputEndpoint {
    const endpoint = this.#outputs.get(name);
    if (endpoint === undefined)
      throw new Error(`output port is not declared: ${String(name)}`);
    return endpoint;
  }
}

export class GraphInterfaceView<
  I extends AnyGraphInterface = AnyGraphInterface,
> {
  private constructor(
    readonly owner: AnyGraph,
    readonly declaration: I,
    key: symbol,
  ) {
    if (key !== createInterfaceView)
      throw new Error("obtain the interface view from its graph");
    Object.freeze(this);
  }
  static [createInterfaceView]<I extends AnyGraphInterface>(
    owner: AnyGraph,
    declaration: I,
  ): GraphInterfaceView<I> {
    return new GraphInterfaceView(owner, declaration, createInterfaceView);
  }
  input<N extends Names<InputsOf<I>> = Extract<Names<InputsOf<I>>, null>>(
    ...args: null extends Names<InputsOf<I>> ? [name?: N] : [name: N]
  ): InputPort<CheckedInputs<Lookup<InputsOf<I>, N>>> {
    const name = args[0] ?? null;
    checkName(name);
    this.declaration.input(name);
    return InputPort[createInput](this.owner, name);
  }
  output<N extends Names<OutputsOf<I>> = Extract<Names<OutputsOf<I>>, null>>(
    ...args: null extends Names<OutputsOf<I>> ? [name?: N] : [name: N]
  ): OutputPort<OutputValue<Lookup<OutputsOf<I>, N>>> {
    const name = args[0] ?? null;
    checkName(name);
    this.declaration.output(name);
    return OutputPort[createOutput](this.owner, name);
  }
}
