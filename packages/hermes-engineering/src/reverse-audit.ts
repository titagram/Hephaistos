export interface ReverseAuditState {
  round: number;
  consecutiveDryRounds: number;
  complete: boolean;
}

export const initialReverseAuditState = (): ReverseAuditState => ({
  round: 0,
  consecutiveDryRounds: 0,
  complete: false,
});

export const validateReverseAuditState = (
  value: unknown,
): ReverseAuditState => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError("reverseAudit must be an object");
  }
  const state = value as Record<string, unknown>;
  const unknown = Object.keys(state).find(
    (key) => !["round", "consecutiveDryRounds", "complete"].includes(key),
  );
  if (unknown !== undefined) {
    throw new TypeError(`unknown reverseAudit field: ${unknown}`);
  }
  if (
    !Number.isSafeInteger(state.round) ||
    (state.round as number) < 0 ||
    (state.round as number) > 5
  ) {
    throw new TypeError(
      "reverseAudit.round must be an integer between 0 and 5",
    );
  }
  if (
    !Number.isSafeInteger(state.consecutiveDryRounds) ||
    (state.consecutiveDryRounds as number) < 0 ||
    (state.consecutiveDryRounds as number) > 2 ||
    (state.consecutiveDryRounds as number) > (state.round as number)
  ) {
    throw new TypeError(
      "reverseAudit.consecutiveDryRounds must be an integer between 0 and 2 and no greater than round",
    );
  }
  if (typeof state.complete !== "boolean") {
    throw new TypeError("reverseAudit.complete must be a boolean");
  }
  const expectedComplete =
    (state.consecutiveDryRounds as number) >= 2 || (state.round as number) >= 5;
  if (state.complete !== expectedComplete) {
    throw new TypeError(
      "reverseAudit.complete is inconsistent with its counters",
    );
  }
  return {
    round: state.round as number,
    consecutiveDryRounds: state.consecutiveDryRounds as number,
    complete: state.complete,
  };
};

export function nextReverseAudit(
  state: ReverseAuditState,
  newConfirmed: number,
): ReverseAuditState {
  const current = validateReverseAuditState(state);
  if (current.complete) {
    throw new TypeError("reverse audit is already complete");
  }
  if (!Number.isSafeInteger(newConfirmed) || newConfirmed < 0) {
    throw new TypeError("newConfirmed must be a non-negative integer");
  }
  const round = current.round + 1;
  const consecutiveDryRounds =
    newConfirmed === 0 ? current.consecutiveDryRounds + 1 : 0;
  return {
    round,
    consecutiveDryRounds,
    complete: consecutiveDryRounds >= 2 || round >= 5,
  };
}
