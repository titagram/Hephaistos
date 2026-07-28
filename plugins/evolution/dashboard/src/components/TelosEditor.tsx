import { React } from "../sdk";
import { TELOS_COLLECTIONS, type TelosCollection, type TelosDraft } from "../telos-model";
import type { TelosItem } from "../types";

void React;

const LABELS: Record<TelosCollection, string> = {
  desired_traits: "Desired traits",
  capability_directions: "Capability directions",
  priorities: "Priorities",
  tradeoffs: "Tradeoffs",
  prohibitions: "Prohibitions",
  success_indicators: "Success indicators",
};

export interface TelosEditorProps {
  draft: TelosDraft;
  parentDigest: string | null;
  disabled: boolean;
  onChange(draft: TelosDraft): void;
}

function itemFor(field: TelosCollection, index: number): TelosItem {
  return { id: `${field.replaceAll("_", "-")}-${index + 1}`, statement: "", tags: [], priority: 3 };
}

function updateItem(items: TelosItem[], index: number, patch: Partial<TelosItem>): TelosItem[] {
  return items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item);
}

function ItemFields({
  item,
  label,
  disabled,
  onChange,
}: {
  item: TelosItem;
  label: string;
  disabled: boolean;
  onChange(patch: Partial<TelosItem>): void;
}): React.ReactElement {
  return (
    <div className="evo-telos-editor__item">
      <label>
        {label} ID
        <input value={item.id} maxLength={64} disabled={disabled} onChange={event => onChange({ id: event.target.value })} />
      </label>
      <label>
        Statement
        <textarea value={item.statement} maxLength={500} disabled={disabled} onChange={event => onChange({ statement: event.target.value })} />
      </label>
      <label>
        Tags (comma separated, up to 16)
        <input
          value={item.tags.join(", ")}
          maxLength={2048}
          disabled={disabled}
          onChange={event => onChange({ tags: event.target.value.split(",").map(tag => tag.trim()).filter(Boolean).slice(0, 16) })}
        />
      </label>
      <label>
        Priority
        <input type="number" min={1} max={5} step={1} value={item.priority} disabled={disabled} onChange={event => onChange({ priority: Number(event.target.value) })} />
      </label>
    </div>
  );
}

export function TelosEditor({ draft, parentDigest, disabled, onChange }: TelosEditorProps): React.ReactElement {
  const replaceCollection = (field: TelosCollection, items: TelosItem[]) => onChange({ ...draft, [field]: items });
  return (
    <section className="evo-telos-editor" aria-labelledby="evo-telos-editor-heading">
      <h2 id="evo-telos-editor-heading">Telos draft</h2>
      <p>Structured fields are validated locally before saving. Saving is inert and never changes the active Telos.</p>
      <dl><div><dt>Selected parent digest</dt><dd>{parentDigest ?? "No active Telos revision"}</dd></div></dl>
      <label>
        Purpose
        <textarea value={draft.purpose} maxLength={1000} disabled={disabled} onChange={event => onChange({ ...draft, purpose: event.target.value })} />
      </label>
      {TELOS_COLLECTIONS.map(field => {
        const minimum = field === "tradeoffs" ? 0 : 1;
        const items = draft[field];
        return (
          <fieldset key={field} disabled={disabled}>
            <legend>{LABELS[field]} ({items.length}/32)</legend>
            {items.map((item, index) => (
              <div key={`${field}-${index}`}>
                <ItemFields item={item} label={`${LABELS[field]} ${index + 1}`} disabled={disabled} onChange={patch => replaceCollection(field, updateItem(items, index, patch))} />
                <button type="button" onClick={() => replaceCollection(field, items.filter((_, itemIndex) => itemIndex !== index))} disabled={disabled || items.length <= minimum}>Remove {LABELS[field]} {index + 1}</button>
              </div>
            ))}
            <button type="button" onClick={() => replaceCollection(field, [...items, itemFor(field, items.length)])} disabled={disabled || items.length >= 32}>Add {LABELS[field]}</button>
          </fieldset>
        );
      })}
      <fieldset disabled={disabled}>
        <legend>Proactivity policy</legend>
        <ItemFields item={draft.proactivity_policy} label="Proactivity policy" disabled={disabled} onChange={patch => onChange({ ...draft, proactivity_policy: { ...draft.proactivity_policy, ...patch } })} />
      </fieldset>
    </section>
  );
}
