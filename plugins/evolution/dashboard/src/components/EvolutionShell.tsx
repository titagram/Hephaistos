import { React, SDK } from "../sdk";
import { useEvolutionSnapshot } from "../state";
import type { EvolutionView } from "../types";
import { initialView, organismFacet, scanJobProgress } from "../view-model";
import { OverviewView } from "./OverviewView";
import { OrganismView } from "./OrganismView";
import { PipelineView } from "./PipelineView";
import { StatusRail } from "./StatusRail";
import { TelosView } from "./TelosView";

void React;

const VIEWS: ReadonlyArray<{ id: EvolutionView; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "organism", label: "Organism" },
  { id: "telos", label: "Telos" },
  { id: "pipeline", label: "Pipeline" },
];

export function EvolutionShell(): React.ReactElement {
  const { useState } = SDK.hooks;
  const [view, setView] = useState<EvolutionView>(initialView());
  const store = useEvolutionSnapshot();
  const facet = store.snapshot === null ? null : organismFacet(store.snapshot, null);

  return (
    <main className="evo-shell">
      <header className="evo-shell__header">
        <h2 className="evo-shell__title">Evolution</h2>
        <p>Local organism · all profiles</p>
        {facet !== null && facet.organism !== null ? (
          <p>
            Organism {facet.organism.id_prefix} · Lineage {facet.organism.lineage_prefix}
          </p>
        ) : null}
      </header>
      <nav className="evo-shell__nav" role="tablist" aria-label="Evolution views">
        {VIEWS.map(item => (
          <button
            key={item.id}
            type="button"
            id={`evo-view-tab-${item.id}`}
            role="tab"
            aria-controls={`evo-view-panel-${item.id}`}
            aria-selected={view === item.id}
            onClick={() => setView(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      {store.warning !== null ? (
        <section className="evo-warning" role="status" aria-live="polite">
          <p>{store.warning.message}</p>
          <button type="button" onClick={() => void store.refresh()} disabled={store.refreshing}>
            Refresh now
          </button>
        </section>
      ) : null}
      {store.activeJob !== null ? (
        <section className="evo-job-strip" role="status" aria-live="polite">
          <p>
            {store.activeJob.kind === "observer_scan"
              ? scanJobProgress(store.activeJob)
              : `${store.activeJob.kind.replaceAll("_", " ")}: ${store.activeJob.state} (${store.activeJob.progress}%)`}
          </p>
        </section>
      ) : null}
      <StatusRail snapshot={store.snapshot} loading={store.loading} />
      <section
        id={`evo-view-panel-${view}`}
        className="evo-shell__content"
        role="tabpanel"
        aria-labelledby={`evo-view-tab-${view}`}
        aria-label={`${VIEWS.find(item => item.id === view)?.label ?? "Evolution"} view`}
      >
        {view === "overview" ? (
          <OverviewView
            snapshot={store.snapshot}
            activeJob={store.activeJob}
            onRefresh={store.refresh}
            onTrackJob={store.trackJob}
            onNavigate={setView}
          />
        ) : view === "organism" ? (
          <OrganismView snapshot={store.snapshot} onRefresh={store.refresh} onTrackJob={store.trackJob} />
        ) : view === "telos" ? (
          <TelosView snapshot={store.snapshot} onRefresh={store.refresh} />
        ) : (
          <PipelineView snapshot={store.snapshot} onRefresh={store.refresh} />
        )}
      </section>
    </main>
  );
}
