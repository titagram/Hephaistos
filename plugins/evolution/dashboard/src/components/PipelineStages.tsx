import { React } from "../sdk";
import type { PipelineStagePresentation } from "../pipeline-model";

void React;

export interface PipelineStagesProps {
  stages: readonly PipelineStagePresentation[];
}

export function PipelineStages({ stages }: PipelineStagesProps): React.ReactElement {
  return (
    <ol className="evo-pipeline-stages" aria-label="Evolution pipeline stages">
      {stages.map(stage => (
        <li key={stage.id} aria-disabled={stage.available ? undefined : true}>
          <h3>{stage.label}</h3>
          <p>{stage.explanation}</p>
        </li>
      ))}
    </ol>
  );
}
