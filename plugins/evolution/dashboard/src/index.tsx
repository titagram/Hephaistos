import { React } from "./sdk";
import { EvolutionShell } from "./components/EvolutionShell";

function EvolutionPlugin() {
  return React.createElement(EvolutionShell);
}

const registry = window.__HERMES_PLUGINS__;
if (registry === undefined) {
  throw new Error("Hermes plugin registry is unavailable");
}

registry.register("evolution", EvolutionPlugin);
