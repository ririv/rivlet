/** A successful result, including a successful `undefined` return. */
export class Some<T> {
  readonly kind = "some";
  constructor(readonly value: T) {
    Object.freeze(this);
  }
  unwrap(): T {
    return this.value;
  }
}

/** A skipped producer, distinct from a successful undefined result. */
export class Nothing {
  readonly kind = "nothing";
  unwrap(): never {
    throw new Error("cannot unwrap a missing result");
  }
}

export const nothing = Object.freeze(new Nothing());
export type Option<T> = Some<T> | Nothing;
