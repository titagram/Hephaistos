import { React, SDK } from "../sdk";

void React;

const DEFAULT_LIMIT = 280;

export interface ExpandableTextProps {
  text: string;
  label: string;
  limit?: number;
}

export function ExpandableText({ text, label, limit = DEFAULT_LIMIT }: ExpandableTextProps): React.ReactElement {
  const { useState } = SDK.hooks;
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > limit;
  const visibleText = expanded || !isLong ? text : `${text.slice(0, limit)}…`;

  return <>
    <span>{visibleText}</span>
    {isLong ? <button type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
      {expanded ? `Show less ${label}` : `Show full ${label}`}
    </button> : null}
  </>;
}
