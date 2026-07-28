import type { TelosDocument, TelosItem, TelosRevision } from "./types";

export const TELOS_COLLECTIONS = [
  "desired_traits",
  "capability_directions",
  "priorities",
  "tradeoffs",
  "prohibitions",
  "success_indicators",
] as const;

export type TelosCollection = typeof TELOS_COLLECTIONS[number];

export interface TelosDraft {
  purpose: string;
  desired_traits: TelosItem[];
  capability_directions: TelosItem[];
  priorities: TelosItem[];
  tradeoffs: TelosItem[];
  prohibitions: TelosItem[];
  proactivity_policy: TelosItem;
  success_indicators: TelosItem[];
}

export interface TelosDiffGroup {
  field: "purpose" | TelosCollection | "proactivity_policy";
  label: string;
  changes: string[];
  added: string[];
  removed: string[];
  changed: string[];
}

const COLLECTION_LABELS: Record<TelosCollection, string> = {
  desired_traits: "Desired traits",
  capability_directions: "Capability directions",
  priorities: "Priorities",
  tradeoffs: "Tradeoffs",
  prohibitions: "Prohibitions",
  success_indicators: "Success indicators",
};

function cloneItem(item: TelosItem): TelosItem {
  return { ...item, tags: [...item.tags] };
}

function cloneItems(items: TelosItem[]): TelosItem[] {
  return items.map(cloneItem);
}

export function createTelosDraft(source: TelosRevision | null): TelosDraft {
  if (source !== null) {
    return {
      purpose: source.purpose,
      desired_traits: cloneItems(source.desired_traits),
      capability_directions: cloneItems(source.capability_directions),
      priorities: cloneItems(source.priorities),
      tradeoffs: cloneItems(source.tradeoffs),
      prohibitions: cloneItems(source.prohibitions),
      proactivity_policy: cloneItem(source.proactivity_policy),
      success_indicators: cloneItems(source.success_indicators),
    };
  }
  return {
    purpose: "",
    desired_traits: [],
    capability_directions: [],
    priorities: [],
    tradeoffs: [],
    prohibitions: [],
    proactivity_policy: { id: "proactivity", statement: "", tags: [], priority: 1 },
    success_indicators: [],
  };
}

export function serializeTelosDraft(
  draft: TelosDraft,
  organismId: string,
  parentDigest: string | null,
): TelosDocument {
  return {
    schema_version: 1,
    organism_id: organismId,
    parent_digest: parentDigest,
    purpose: draft.purpose,
    desired_traits: cloneItems(draft.desired_traits),
    capability_directions: cloneItems(draft.capability_directions),
    priorities: cloneItems(draft.priorities),
    tradeoffs: cloneItems(draft.tradeoffs),
    prohibitions: cloneItems(draft.prohibitions),
    proactivity_policy: cloneItem(draft.proactivity_policy),
    success_indicators: cloneItems(draft.success_indicators),
  };
}

const ITEM_ID = /^[a-z][a-z0-9_.-]{0,63}$/;
const TAG = /^[a-z][a-z0-9_.-]{0,127}$/;

function itemErrors(label: string, item: TelosItem): string[] {
  const errors: string[] = [];
  if (!ITEM_ID.test(item.id)) errors.push(`${label} ID must use lowercase letters, digits, dots, underscores, or hyphens.`);
  if (item.statement.trim().length < 1 || item.statement.length > 500) errors.push(`${label} statement must be 1–500 characters.`);
  if (!Number.isInteger(item.priority) || item.priority < 1 || item.priority > 5) errors.push(`${label} priority must be an integer from 1 to 5.`);
  if (item.tags.length > 16 || item.tags.some(tag => !TAG.test(tag))) errors.push(`${label} tags must contain at most 16 lowercase identifiers.`);
  return errors;
}

export function validateTelosDraft(draft: TelosDraft): string[] {
  const errors: string[] = [];
  if (draft.purpose.trim().length < 1 || draft.purpose.length > 1000) errors.push("Purpose must be 1–1000 characters.");
  const seen = new Set<string>();
  for (const field of TELOS_COLLECTIONS) {
    const items = draft[field];
    const minimum = field === "tradeoffs" ? 0 : 1;
    if (items.length < minimum || items.length > 32) errors.push(`${COLLECTION_LABELS[field]} must contain ${minimum}–32 items.`);
    for (const item of items) {
      errors.push(...itemErrors(`${COLLECTION_LABELS[field]} ${item.id || "item"}`, item));
      if (seen.has(item.id)) errors.push(`Item ID ${item.id} must be unique across Telos.`);
      seen.add(item.id);
    }
  }
  errors.push(...itemErrors("Proactivity policy", draft.proactivity_policy));
  if (seen.has(draft.proactivity_policy.id)) errors.push(`Item ID ${draft.proactivity_policy.id} must be unique across Telos.`);
  return errors;
}

function itemChanged(left: TelosItem, right: TelosItem): boolean {
  return left.statement !== right.statement
    || left.priority !== right.priority
    || left.tags.length !== right.tags.length
    || left.tags.some((tag, index) => tag !== right.tags[index]);
}

function collectionDiff(field: TelosCollection, before: TelosItem[], after: TelosItem[]): TelosDiffGroup {
  const beforeById = new Map(before.map(item => [item.id, item]));
  const afterById = new Map(after.map(item => [item.id, item]));
  return {
    field,
    label: COLLECTION_LABELS[field],
    changes: [],
    added: after.filter(item => !beforeById.has(item.id)).map(item => item.id),
    removed: before.filter(item => !afterById.has(item.id)).map(item => item.id),
    changed: after.filter(item => {
      const previous = beforeById.get(item.id);
      return previous !== undefined && itemChanged(previous, item);
    }).map(item => item.id),
  };
}

export function semanticTelosDiff(before: TelosRevision, after: TelosRevision): TelosDiffGroup[] {
  const groups: TelosDiffGroup[] = [];
  if (before.purpose !== after.purpose) {
    groups.push({ field: "purpose", label: "Purpose", changes: ["Purpose changed"], added: [], removed: [], changed: [] });
  }
  for (const field of TELOS_COLLECTIONS) {
    const group = collectionDiff(field, before[field], after[field]);
    if (group.added.length > 0 || group.removed.length > 0 || group.changed.length > 0) groups.push(group);
  }
  if (itemChanged(before.proactivity_policy, after.proactivity_policy)) {
    groups.push({
      field: "proactivity_policy",
      label: "Proactivity policy",
      changes: ["Proactivity policy changed"],
      added: [],
      removed: [],
      changed: [after.proactivity_policy.id],
    });
  }
  return groups;
}

export function isExactConfirmationPhrase(value: string, requiredPhrase: string): boolean {
  return value === requiredPhrase;
}

export function confirmationConsequences(action: "activate" | "rollback"): string {
  return action === "activate"
    ? "This activates the selected inert Telos revision for the local organism. It does not alter its immutable draft."
    : "This restores the selected prior Telos revision as the local organism's active Telos. It does not delete newer revisions.";
}

function statusFromError(error: unknown): number | null {
  if (typeof error === "object" && error !== null && "status" in error) {
    const value = Reflect.get(error, "status");
    if (typeof value === "number") return value;
  }
  if (error instanceof Error && /^409(?::|\s|$)/.test(error.message)) return 409;
  return null;
}

export interface StaleTransitionRecovery {
  close: boolean;
  refresh: boolean;
  retry: false;
  warning: string;
}

export function staleTransitionRecovery(error: unknown): StaleTransitionRecovery | null {
  if (statusFromError(error) !== 409) return null;
  return {
    close: true,
    refresh: true,
    retry: false,
    warning: "The organism changed elsewhere. Refresh completed before another Telos transition can be prepared.",
  };
}
