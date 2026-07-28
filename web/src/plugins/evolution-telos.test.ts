import { describe, expect, it } from "vitest";
import {
  confirmationConsequences,
  createTelosDraft,
  isExactConfirmationPhrase,
  semanticTelosDiff,
  serializeTelosDraft,
  staleTransitionRecovery,
  validateTelosDraft,
} from "../../../plugins/evolution/dashboard/src/telos-model";
import type { TelosRevision } from "../../../plugins/evolution/dashboard/src/types";

const ORGANISM_ID = "00000000-0000-4000-8000-000000000000";
const ACTIVE_DIGEST = "a".repeat(64);

function revision(overrides: Partial<TelosRevision> = {}): TelosRevision {
  return {
    digest: ACTIVE_DIGEST,
    parent_digest: null,
    purpose: "Help the operator safely.",
    desired_traits: [{ id: "careful", statement: "Be careful.", tags: ["safety"], priority: 1 }],
    capability_directions: [{ id: "local", statement: "Prefer local capability.", tags: [], priority: 2 }],
    priorities: [{ id: "operator", statement: "Serve the operator.", tags: [], priority: 1 }],
    tradeoffs: [],
    prohibitions: [{ id: "no-leaks", statement: "Do not expose private data.", tags: ["safety"], priority: 1 }],
    proactivity_policy: { id: "consent", statement: "Ask before consequential work.", tags: [], priority: 1 },
    success_indicators: [{ id: "useful", statement: "Work is useful.", tags: [], priority: 2 }],
    ...overrides,
  };
}

describe("Telos control-center model", () => {
  it("serializes every actual API Telos field with the selected active parent digest", () => {
    const draft = createTelosDraft(revision());
    draft.purpose = "Help the operator with bounded, safe actions.";
    const document = serializeTelosDraft(draft, ORGANISM_ID, ACTIVE_DIGEST);

    expect(document).toEqual({
      schema_version: 1,
      organism_id: ORGANISM_ID,
      parent_digest: ACTIVE_DIGEST,
      purpose: "Help the operator with bounded, safe actions.",
      desired_traits: draft.desired_traits,
      capability_directions: draft.capability_directions,
      priorities: draft.priorities,
      tradeoffs: draft.tradeoffs,
      prohibitions: draft.prohibitions,
      proactivity_policy: draft.proactivity_policy,
      success_indicators: draft.success_indicators,
    });
  });

  it("keeps an inert saved draft separate from the active revision", () => {
    const saved = serializeTelosDraft(createTelosDraft(revision()), ORGANISM_ID, ACTIVE_DIGEST);

    expect(saved.parent_digest).toBe(ACTIVE_DIGEST);
    expect(saved).not.toHaveProperty("active_digest");
  });

  it("validates structured editor fields before a server 422 can occur", () => {
    const draft = createTelosDraft(revision());
    draft.priorities[0] = { ...draft.priorities[0]!, statement: "", priority: 9 };

    expect(validateTelosDraft(draft)).toEqual(expect.arrayContaining([
      expect.stringMatching(/priorit.*statement/i),
      expect.stringMatching(/priorit.*1.*5/i),
    ]));
  });

  it("groups semantic Telos changes by real document field", () => {
    const before = revision();
    const after = revision({
      purpose: "Help the operator safely and clearly.",
      desired_traits: [{ id: "careful", statement: "Be careful and explicit.", tags: ["safety"], priority: 1 }],
      tradeoffs: [{ id: "speed", statement: "Prefer accuracy over speed.", tags: [], priority: 3 }],
    });

    expect(semanticTelosDiff(before, after)).toEqual(expect.arrayContaining([
      expect.objectContaining({ field: "purpose", changes: ["Purpose changed"] }),
      expect.objectContaining({ field: "desired_traits", changed: ["careful"] }),
      expect.objectContaining({ field: "tradeoffs", added: ["speed"] }),
    ]));
  });

  it("requires the exact server-issued confirmation phrase", () => {
    const phrase = "ACTIVATE 00000000 bbbbbbbbbbbb nonce";

    expect(isExactConfirmationPhrase(phrase, phrase)).toBe(true);
    expect(isExactConfirmationPhrase(`${phrase} `, phrase)).toBe(false);
    expect(isExactConfirmationPhrase("activate 00000000 bbbbbbbbbbbb nonce", phrase)).toBe(false);
  });

  it("explains activate and rollback with different consequences", () => {
    expect(confirmationConsequences("activate")).toMatch(/activates/i);
    expect(confirmationConsequences("rollback")).toMatch(/restores/i);
    expect(confirmationConsequences("activate")).not.toBe(confirmationConsequences("rollback"));
  });

  it("closes a stale transition, refreshes once, and never retries it", () => {
    expect(staleTransitionRecovery({ status: 409 })).toEqual({
      close: true,
      refresh: true,
      retry: false,
      warning: "The organism changed elsewhere. Refresh completed before another Telos transition can be prepared.",
    });
  });
});
