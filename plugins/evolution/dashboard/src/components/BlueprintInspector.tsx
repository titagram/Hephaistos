import { React, SDK } from "../sdk";
import { sortedAuditEvents } from "../pipeline-model";
import type { AuditEvent, PipelineBlueprint } from "../types";

void React;

const HYPOTHESIS_LIMIT = 280;

export interface BlueprintInspectorProps {
  blueprint: PipelineBlueprint | null;
  auditEvents: readonly AuditEvent[];
}

export function BlueprintInspector({ blueprint, auditEvents }: BlueprintInspectorProps): React.ReactElement {
  const { useState } = SDK.hooks;
  const [expanded, setExpanded] = useState(false);
  if (blueprint === null) {
    return <aside className="evo-pipeline-inspector" aria-label="Blueprint inspector"><p>Select an immutable blueprint to inspect it.</p></aside>;
  }
  const hypothesis = expanded || blueprint.capability_hypothesis.length <= HYPOTHESIS_LIMIT
    ? blueprint.capability_hypothesis
    : `${blueprint.capability_hypothesis.slice(0, HYPOTHESIS_LIMIT)}…`;
  const history = sortedAuditEvents(auditEvents.filter(event => event.attempt_id === blueprint.attempt_id));
  return (
    <aside className="evo-pipeline-inspector" aria-labelledby="evo-blueprint-inspector-heading">
      <h2 id="evo-blueprint-inspector-heading">Immutable blueprint</h2>
      <dl>
        <div><dt>Blueprint ID</dt><dd>{blueprint.blueprint_id}</dd></div>
        <div><dt>Canonical digest</dt><dd>{blueprint.canonical_digest}</dd></div>
        <div><dt>Active Telos digest</dt><dd>{blueprint.active_telos_digest}</dd></div>
        <div><dt>State</dt><dd>{blueprint.state}</dd></div>
      </dl>
      <section aria-labelledby="evo-blueprint-scope-heading">
        <h3 id="evo-blueprint-scope-heading">Requested scope</h3>
        <p>{hypothesis}</p>
        {blueprint.capability_hypothesis.length > HYPOTHESIS_LIMIT ? <button type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>{expanded ? "Show less requested scope" : "Show full requested scope"}</button> : null}
        <h4>Proposed component classes</h4>
        {blueprint.proposed_component_classes.length > 0 ? <ul>{blueprint.proposed_component_classes.map(component => <li key={component}>{component}</li>)}</ul> : <p>No component class is requested.</p>}
      </section>
      <section aria-labelledby="evo-blueprint-auth-heading">
        <h3 id="evo-blueprint-auth-heading">Authorization history</h3>
        {history.length === 0 ? <p>No durable authorization history is recorded for this attempt.</p> : <ol>{history.map(event => <li key={event.event_id}>#{event.sequence} · {event.summary}</li>)}</ol>}
      </section>
    </aside>
  );
}
