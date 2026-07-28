import { React } from "../sdk";
import type { EvolutionSnapshot } from "../types";
import { readinessBlockers } from "../view-model";

void React;

export interface StatusRailProps {
  snapshot: EvolutionSnapshot | null;
  loading: boolean;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}

export function StatusRail({ snapshot, loading }: StatusRailProps): React.ReactElement {
  if (snapshot === null) {
    return (
      <section className="evo-status-rail" aria-label="Evolution status" aria-busy={loading}>
        <p>{loading ? "Loading local organism status…" : "No organism status is available."}</p>
      </section>
    );
  }

  const blockers = readinessBlockers(snapshot);
  return (
    <section className="evo-status-rail" aria-label="Evolution status">
      <p>Overall status: {humanize(snapshot.state)}</p>
      {blockers.length === 0 ? (
        <p>All monitored local organism systems are ready.</p>
      ) : (
        <ol>
          {blockers.map(blocker => (
            <li key={blocker.source}>
              {blocker.label}: {humanize(blocker.state)}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
