import { blueprintAction } from "../pipeline-model";
import { React, SDK } from "../sdk";
import type { PipelineBlueprint, PipelineSuggestion } from "../types";

void React;

const SUMMARY_LIMIT = 240;

export interface SuggestionInspectorProps {
  suggestion: PipelineSuggestion | null;
  blueprints: readonly PipelineBlueprint[];
  disabled: boolean;
  onCreateBlueprint(suggestion: PipelineSuggestion): void;
  onResearch(suggestion: PipelineSuggestion): void;
}

export function SuggestionInspector({
  suggestion,
  blueprints,
  disabled,
  onCreateBlueprint,
  onResearch,
}: SuggestionInspectorProps): React.ReactElement {
  const { useState } = SDK.hooks;
  const [expanded, setExpanded] = useState(false);

  if (suggestion === null) {
    return <aside className="evo-pipeline-inspector" aria-label="Suggestion inspector"><p>Select a local suggestion to inspect it.</p></aside>;
  }

  const action = blueprintAction(suggestion, blueprints);
  const summary = expanded || suggestion.summary.length <= SUMMARY_LIMIT
    ? suggestion.summary
    : `${suggestion.summary.slice(0, SUMMARY_LIMIT)}…`;
  return (
    <aside className="evo-pipeline-inspector" aria-labelledby="evo-suggestion-inspector-heading">
      <h2 id="evo-suggestion-inspector-heading">Suggestion</h2>
      <dl>
        <div><dt>State</dt><dd>{suggestion.state}</dd></div>
        <div><dt>Score</dt><dd>{suggestion.score.toFixed(2)}</dd></div>
        <div><dt>Telos alignment</dt><dd>{suggestion.telos_alignment.toFixed(2)}</dd></div>
        <div><dt>Evidence facts</dt><dd>{suggestion.observation_count} observations across {suggestion.distinct_session_count} sessions</dd></div>
      </dl>
      <section aria-labelledby="evo-suggestion-summary-heading">
        <h3 id="evo-suggestion-summary-heading">Local summary</h3>
        <p>{summary}</p>
        {suggestion.summary.length > SUMMARY_LIMIT ? (
          <button type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
            {expanded ? "Show less local summary" : "Show full local summary"}
          </button>
        ) : null}
      </section>
      <section aria-labelledby="evo-public-research-heading">
        <h3 id="evo-public-research-heading">Public research references</h3>
        <p>No public research references are recorded for this suggestion yet.</p>
        <button type="button" onClick={() => onResearch(suggestion)} disabled={disabled}>Research public documentation</button>
      </section>
      <section aria-labelledby="evo-blueprint-gate-heading">
        <h3 id="evo-blueprint-gate-heading">Blueprint gate</h3>
        <p>{action.message}</p>
        {action.kind === "create" ? (
          <button type="button" onClick={() => onCreateBlueprint(suggestion)} disabled={disabled}>Create immutable blueprint</button>
        ) : null}
      </section>
    </aside>
  );
}
