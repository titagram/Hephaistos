import { evolutionApi, type EvolutionCollector } from "../api";
import { React, SDK } from "../sdk";
import type {
  EvolutionJob,
  MutationContext,
  RevisionDiffResponse,
  RevisionListResponse,
} from "../types";

void React;

const COLLECTORS: ReadonlyArray<{ id: EvolutionCollector; label: string }> = [
  { id: "source", label: "Source" },
  { id: "capabilities", label: "Capabilities" },
  { id: "runtime", label: "Runtime" },
  { id: "contracts", label: "Contracts" },
  { id: "dependencies", label: "Dependencies" },
  { id: "experience", label: "Experience" },
];

export type RevisionDialogMode = "rebuild" | "compare";

export interface RevisionDialogProps {
  mode: RevisionDialogMode;
  context: MutationContext | null;
  onClose(): void;
  onJobStarted(job: EvolutionJob): void;
}

function mutationErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The requested local operation could not be completed.";
}

function DiffSummary({ diff }: { diff: RevisionDiffResponse | null }): React.ReactElement | null {
  if (diff === null) return null;
  return (
    <section className="evo-revision-dialog__diff" aria-live="polite">
      <h3>Semantic revision diff</h3>
      <dl>
        <div><dt>Added capabilities</dt><dd>{diff.added_capabilities.length}</dd></div>
        <div><dt>Removed capabilities</dt><dd>{diff.removed_capabilities.length}</dd></div>
        <div><dt>Changed states</dt><dd>{diff.changed_state.length}</dd></div>
        <div><dt>Dependency changes</dt><dd>{diff.dependency_changes.length}</dd></div>
        <div><dt>Invariant impact</dt><dd>{diff.invariant_impact.length}</dd></div>
        <div><dt>Runtime changes</dt><dd>{diff.runtime_changes.length}</dd></div>
      </dl>
      {diff.truncated ? <p>Diff rows are bounded; this semantic comparison is truncated.</p> : null}
    </section>
  );
}

function RebuildContents({ context, onClose, onJobStarted }: Pick<RevisionDialogProps, "context" | "onClose" | "onJobStarted">): React.ReactElement {
  const { useState } = SDK.hooks;
  const [collectors, setCollectors] = useState<EvolutionCollector[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleCollector = (collector: EvolutionCollector) => {
    setCollectors(selected => selected.includes(collector)
      ? selected.filter(item => item !== collector)
      : [...selected, collector]);
  };

  const submit = async () => {
    if (context === null || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const job = await evolutionApi.rebuild({ ...context, force: false, collectors });
      onJobStarted(job);
      onClose();
    } catch (nextError) {
      setError(mutationErrorMessage(nextError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <p>This queues a read-only local collection job. It does not activate Telos or change a generation.</p>
      {context === null ? <p>Loading the current mutation context…</p> : (
        <dl>
          <div><dt>Organism</dt><dd>{context.organism_id}</dd></div>
          <div><dt>Current snapshot digest</dt><dd>{context.expected_snapshot_digest}</dd></div>
        </dl>
      )}
      <fieldset disabled={context === null || submitting}>
        <legend>Optional collectors</legend>
        {COLLECTORS.map(collector => (
          <label key={collector.id}>
            <input
              type="checkbox"
              checked={collectors.includes(collector.id)}
              onChange={() => toggleCollector(collector.id)}
            />
            {collector.label}
          </label>
        ))}
      </fieldset>
      {error !== null ? <p role="alert">{error}</p> : null}
      <footer>
        <button type="button" onClick={onClose} disabled={submitting}>Cancel</button>
        <button type="button" onClick={() => void submit()} disabled={context === null || submitting}>
          {submitting ? "Queueing…" : "Rebuild organism"}
        </button>
      </footer>
    </>
  );
}

function CompareContents(): React.ReactElement {
  const { useEffect, useState } = SDK.hooks;
  const [revisions, setRevisions] = useState<RevisionListResponse | null>(null);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [diff, setDiff] = useState<RevisionDiffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let current = true;
    void evolutionApi.revisions(50).then(response => {
      if (!current) return;
      setRevisions(response);
      setLeft(response.items[1]?.revision_id ?? response.items[0]?.revision_id ?? "");
      setRight(response.items[0]?.revision_id ?? "");
      setLoading(false);
    }).catch(nextError => {
      if (!current) return;
      setError(mutationErrorMessage(nextError));
      setLoading(false);
    });
    return () => { current = false; };
  }, []);

  const compare = async () => {
    if (left === "" || right === "" || left === right) return;
    setError(null);
    try {
      setDiff(await evolutionApi.diff(left, right));
    } catch (nextError) {
      setError(mutationErrorMessage(nextError));
    }
  };

  if (loading) return <p>Loading immutable revisions…</p>;
  if (revisions === null || revisions.items.length < 2) return <p>Two immutable organism revisions are required for a semantic comparison.</p>;

  return (
    <>
      <p>Compare two immutable local organism revisions.</p>
      <label>
        Earlier revision
        <select value={left} onChange={event => setLeft(event.target.value)}>
          {revisions.items.map(item => <option key={item.revision_id} value={item.revision_id}>{item.revision_id}</option>)}
        </select>
      </label>
      <label>
        Later revision
        <select value={right} onChange={event => setRight(event.target.value)}>
          {revisions.items.map(item => <option key={item.revision_id} value={item.revision_id}>{item.revision_id}</option>)}
        </select>
      </label>
      {revisions.truncated ? <p>Revision history is bounded. Choose from the listed immutable revisions.</p> : null}
      {error !== null ? <p role="alert">{error}</p> : null}
      <button type="button" onClick={() => void compare()} disabled={left === "" || right === "" || left === right}>Compare revisions</button>
      <DiffSummary diff={diff} />
    </>
  );
}

export function RevisionDialog({ mode, context, onClose, onJobStarted }: RevisionDialogProps): React.ReactElement {
  const title = mode === "rebuild" ? "Rebuild organism" : "Compare revisions";
  return (
    <div className="evo-revision-dialog" role="dialog" aria-modal="true" aria-labelledby="evo-revision-dialog-title">
      <section className="evo-revision-dialog__content">
        <header>
          <h2 id="evo-revision-dialog-title">{title}</h2>
          <button type="button" onClick={onClose} aria-label={`Close ${title}`}>Close</button>
        </header>
        {mode === "rebuild"
          ? <RebuildContents context={context} onClose={onClose} onJobStarted={onJobStarted} />
          : <CompareContents />}
      </section>
    </div>
  );
}
