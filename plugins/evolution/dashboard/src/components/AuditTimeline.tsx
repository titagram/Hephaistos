import { React } from "../sdk";
import type { AuditResponse } from "../types";
import { ExpandableText } from "./ExpandableText";

void React;

export interface AuditTimelineProps {
  audit: AuditResponse | null;
  loading: boolean;
  error: string | null;
}

export function AuditTimeline({ audit, loading, error }: AuditTimelineProps): React.ReactElement {
  return (
    <section className="evo-audit" aria-labelledby="evo-audit-heading">
      <h2 id="evo-audit-heading">Bounded audit history</h2>
      {loading ? <p role="status">Loading bounded durable audit history…</p> : null}
      {error !== null ? <p role="status">{error}</p> : null}
      {!loading && error === null && audit !== null && audit.events.length === 0 ? <p>No durable audit events are available.</p> : null}
      {audit !== null && audit.events.length > 0 ? (
        <ol>
          {audit.events.map(event => (
            <li key={event.event_id}>
              <time dateTime={event.created_at}>{event.created_at}</time> · <ExpandableText text={event.summary} label="audit summary" />
            </li>
          ))}
        </ol>
      ) : null}
      {audit?.truncated ? <p>This bounded audit history begins with the earliest available events.</p> : null}
    </section>
  );
}
