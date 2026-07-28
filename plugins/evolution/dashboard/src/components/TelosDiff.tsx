import { React } from "../sdk";
import { semanticTelosDiff } from "../telos-model";
import type { TelosRevision } from "../types";

void React;

export interface TelosDiffProps {
  current: TelosRevision | null;
  target: TelosRevision | null;
}

export function TelosDiff({ current, target }: TelosDiffProps): React.ReactElement | null {
  if (current === null || target === null) return null;
  const groups = semanticTelosDiff(current, target);
  return (
    <section className="evo-telos-diff" aria-labelledby="evo-telos-diff-heading">
      <h3 id="evo-telos-diff-heading">Semantic Telos diff</h3>
      {groups.length === 0 ? <p>No semantic Telos changes are present.</p> : (
        <ul>
          {groups.map(group => (
            <li key={group.field}>
              <strong>{group.label}</strong>
              {group.changes.map(change => <p key={change}>{change}</p>)}
              {group.added.length > 0 ? <p>Added: {group.added.join(", ")}</p> : null}
              {group.removed.length > 0 ? <p>Removed: {group.removed.join(", ")}</p> : null}
              {group.changed.length > 0 ? <p>Changed: {group.changed.join(", ")}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
