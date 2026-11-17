/** A constructor used only to check an injected object's type, never to create it. */
export type SharedStateType<T extends object> = abstract new (
  ...args: never[]
) => T;

export interface SharedAccess {
  <T extends object>(expected: SharedStateType<T>): T;
}

/** One accessor per execution tree; no implicit allocation, copying or serialization. */
export function sharedAccess(state: object | undefined): SharedAccess {
  if (
    state !== undefined &&
    (state === null ||
      (typeof state !== "object" && typeof state !== "function"))
  ) {
    throw new TypeError("shared state must be an object");
  }
  return <T extends object>(expected: SharedStateType<T>): T => {
    if (state === undefined)
      throw new Error("no shared state was provided for this execution");
    if (!(state instanceof expected))
      throw new TypeError("graph shared state type mismatch");
    return state;
  };
}
