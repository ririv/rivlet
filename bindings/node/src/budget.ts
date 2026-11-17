/** Shared admission for actual actions across generated graph sessions. */
export interface Budget {
  readonly maximum: number;
  running: number;
}
interface Waiter {
  readonly groups: readonly Budget[];
  readonly signal: AbortSignal;
  readonly resolve: (release: () => void) => void;
  readonly reject: (error: unknown) => void;
  readonly abort: () => void;
}
export class ActionBudget {
  readonly #waiting: Waiter[] = [];
  acquire(groups: readonly Budget[], signal: AbortSignal): Promise<() => void> {
    return new Promise((resolve, reject) => {
      signal.throwIfAborted();
      const waiter: Waiter = {
        groups,
        signal,
        resolve,
        reject,
        abort: () => {
          const index = this.#waiting.indexOf(waiter);
          if (index !== -1) this.#waiting.splice(index, 1);
          reject(signal.reason);
          this.#pump();
        },
      };
      signal.addEventListener("abort", waiter.abort, { once: true });
      this.#waiting.push(waiter);
      this.#pump();
    });
  }
  #pump(): void {
    for (let index = 0; index < this.#waiting.length;) {
      const waiter = this.#waiting[index];
      if (waiter === undefined) throw new Error("invalid budget queue");
      if (waiter.groups.some((group) => group.running >= group.maximum)) {
        index++;
        continue;
      }
      this.#waiting.splice(index, 1);
      waiter.signal.removeEventListener("abort", waiter.abort);
      for (const group of waiter.groups) group.running++;
      let released = false;
      waiter.resolve(() => {
        if (released) throw new Error("action budget released twice");
        released = true;
        for (const group of waiter.groups) group.running--;
        this.#pump();
      });
    }
  }
}
