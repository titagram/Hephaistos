import { React } from "../sdk";
import type { EvolutionSnapshot, EvolutionView } from "../types";
import { coveragePresentation, priorityBlockerLinks } from "../view-model";

void React;

export interface ReadinessSummaryProps {
  snapshot: EvolutionSnapshot | null;
  onNavigate(view: EvolutionView): void;
}

function readinessStatement(snapshot: EvolutionSnapshot): string {
  return priorityBlockerLinks(snapshot).length === 0
    ? "This local organism is ready for supervised evolution."
    : "Evolution readiness needs attention before the next change.";
}

export function ReadinessSummary({ snapshot, onNavigate }: ReadinessSummaryProps): React.ReactElement {
  if (snapshot === null) {
    return <section className="evo-readiness" aria-busy="true"><h2>Readiness</h2><p>Loading local readiness…</p></section>;
  }

  const blockers = priorityBlockerLinks(snapshot);
  const coverage = coveragePresentation(snapshot.gnothi);
  return (
    <section className="evo-readiness" aria-labelledby="evo-readiness-heading">
      <h2 id="evo-readiness-heading">Readiness</h2>
      <p>{readinessStatement(snapshot)}</p>
      <p><span aria-hidden="true">{coverage.icon}</span> {coverage.text}</p>
      <dl>
        <div><dt>Observer</dt><dd>{snapshot.observer.enabled ? "Enabled" : "Paused"} · {snapshot.observer.state}</dd></div>
        <div><dt>Telos</dt><dd>{snapshot.telos.state}</dd></div>
      </dl>
      {blockers.length > 0 ? (
        <section aria-labelledby="evo-blockers-heading">
          <h3 id="evo-blockers-heading">Priority blockers</h3>
          <ul>
            {blockers.map(blocker => (
              <li key={blocker.source}>
                <a
                  href={`#${blocker.view}`}
                  onClick={event => { event.preventDefault(); onNavigate(blocker.view); }}
                >
                  {blocker.label}: {blocker.state}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
