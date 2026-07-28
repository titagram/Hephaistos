import { React } from "../sdk";
import { edgeStyleClass, nodeStateClass } from "../graph-model";
import type { GraphEdge, GraphNode } from "../types";

void React;

export interface OrganismListProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  onSelect(id: string): void;
}

function relationText(edge: GraphEdge, nodesById: Map<string, GraphNode>): string {
  const target = nodesById.get(edge.to);
  return `${edge.kind.replaceAll("_", " ")} ${target?.label ?? edge.to}`;
}

export function OrganismList({ nodes, edges, selectedId, onSelect }: OrganismListProps): React.ReactElement {
  const nodesById = new Map(nodes.map(node => [node.id, node]));
  return (
    <section className="evo-organism-list" aria-label="Organism graph as a list">
      <p className="evo-organism-list__summary">{nodes.length} nodes · {edges.length} relationships</p>
      {nodes.length === 0 ? (
        <p>No graph nodes match the active filters.</p>
      ) : (
        <ol className="evo-organism-list__items">
          {nodes.map(node => {
            const outgoing = edges.filter(edge => edge.from === node.id);
            return (
              <li key={node.id} className="evo-organism-list__item">
                <button
                  type="button"
                  className={node.id === selectedId ? "evo-organism-list__select is-selected" : "evo-organism-list__select"}
                  aria-pressed={node.id === selectedId}
                  onClick={() => onSelect(node.id)}
                >
                  <span>{node.label}</span>
                  <span>{node.kind} · {nodeStateClass(node).replace("evo-node--", "")}</span>
                  <span>{node.id}</span>
                </button>
                {outgoing.length > 0 ? (
                  <ul className="evo-organism-list__relations" aria-label={`Relationships from ${node.label}`}>
                    {outgoing.map(edge => (
                      <li key={edge.id} className={edgeStyleClass(edge.kind)}>{relationText(edge, nodesById)}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
