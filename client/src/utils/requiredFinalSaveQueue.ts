export class RequiredFinalSaveQueue<T> {
  private tail: Promise<void> = Promise.resolve();
  private finalization: Promise<T> | null = null;

  enqueue(operation: () => Promise<T>): Promise<T> {
    const result = this.tail.then(operation, operation);
    this.tail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  finalize(
    finalSave: () => Promise<T>,
    afterSave: (saved: T) => Promise<void>,
  ): Promise<T> {
    if (this.finalization) return this.finalization;
    const saved = this.enqueue(finalSave);
    const finalization = saved.then(async (result) => {
      await afterSave(result);
      return result;
    });
    this.finalization = finalization;
    const clearFinalization = () => {
      if (this.finalization === finalization) {
        this.finalization = null;
      }
    };
    void finalization.then(clearFinalization, clearFinalization);
    return finalization;
  }

  isFinalizing(): boolean {
    return this.finalization !== null;
  }
}
