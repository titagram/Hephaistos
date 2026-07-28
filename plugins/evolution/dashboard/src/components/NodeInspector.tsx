import { React } from "../sdk";
import { nodeStateClass } from "../graph-model";
import type { GraphEdge, GraphNode } from "../types";

void React;

const MAX_INSPECTOR_ROWS = 12;
const MAX_EVIDENCE_REFS = 8;

export interface NodeInspectorProps {
  node: GraphNode | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  blockers: GraphNode[];
}

function nodeById(nodes: GraphNode[], id: string): GraphNode | null {
  return nodes.find(node => node.id === id) ?? null;
}

function NodeRows({ title, values }: { title: string; values: GraphNode[] }): React.ReactElement {
  return (
    <section className="evo-node-inspector__section">
      <h3>{title}</h3>
      {values.length === 0 ? <p>None reported.</p> : (
        <ul>
          {values.slice(0, MAX_INSPECTOR_ROWS).map(item => <li key={item.id}>{item.label} <span>{item.kind}</span></li>)}
        </ul>
      )}
    </section>
  );
}

function relatedNodes(node: GraphNode, edges: GraphEdge[], nodes: GraphNode[], direction: "inbound" | "outbound"): GraphNode[] {
  const ids = edges
    .filter(edge => direction === "outbound" ? edge.from === node.id : edge.to === node.id)
    .map(edge => direction === "outbound" ? edge.to : edge.from);
  return [...new Set(ids)]
    .map(id => nodeById(nodes, id))
    .filter((value): value is GraphNode => value !== null)
    .sort((left, right) => left.id.localeCompare(right.id));
}

export function NodeInspector({ node, nodes, edges, blockers }: NodeInspectorProps): React.ReactElement {
  if (node === null) {
    return (
      <aside id="evo-node-inspector" className="evo-node-inspector" tabIndex={-1} aria-label="Selected organism node">
        <h2>Selected node</h2>
        <p>Select a graph node or a list row to inspect bounded local details.</p>
      </aside>
    );
  }

  const dependencies = relatedNodes(node, edges, nodes, "outbound");
  const dependents = relatedNodes(node, edges, nodes, "inbound");
  const affectedCapabilities = [...dependencies, ...dependents]
    .filter(item => item.kind === "capability")
    .filter((item, index, list) => list.findIndex(candidate => candidate.id === item.id) === index);
  const nodeBlockers = blockers.filter(blocker => blocker.id === node.id || dependencies.some(item => item.id === blocker.id) || dependents.some(item => item.id === blocker.id));

  return (
    <aside id="evo-node-inspector" className="evo-node-inspector" tabIndex={-1} aria-label={`Selected node ${node.label}`}>
      <h2>Selected node</h2>
      <p className={nodeStateClass(node)}>{node.label}</p>
      <dl>
        <div><dt>Stable ID</dt><dd>{node.id}</dd></div>
        <div><dt>Kind</dt><dd>{node.kind}</dd></div>
        <div><dt>Owner</dt><dd>{node.owner_class}</dd></div>
        <div><dt>Generation scope</dt><dd>{node.generation_scope}</dd></div>
      </dl>
      <section className="evo-node-inspector__section">
        <h3>Dimensions</h3>
        {Object.keys(node.state).length === 0 ? <p>Unknown.</p> : (
          <dl>
            {Object.entries(node.state).slice(0, MAX_INSPECTOR_ROWS).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value ? "yes" : "no"}</dd></div>)}
          </dl>
        )}
      </section>
      <section className="evo-node-inspector__section">
        <h3>Evidence references</h3>
        <p>Evidence freshness is not provided by this bounded graph response.</p>
        {node.evidence_refs.length === 0 ? <p>None reported.</p> : (
          <ul>{node.evidence_refs.slice(0, MAX_EVIDENCE_REFS).map(reference => <li key={reference}>{reference}</li>)}</ul>
        )}
      </section>
      <NodeRows title="Dependencies" values={dependencies} />
      <NodeRows title="Dependents" values={dependents} />
      <NodeRows title="Blockers" values={nodeBlockers} />
      <NodeRows title="Affected capabilities" values={affectedCapabilities} />
    </aside>
  );
}
