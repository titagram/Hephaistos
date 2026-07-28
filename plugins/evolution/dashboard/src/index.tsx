import { React } from "./sdk";

function EvolutionPlugin() {
  return React.createElement("main", { className: "evo-shell" }, "Evolution");
}

const registry = window.__HERMES_PLUGINS__;
if (registry === undefined) {
  throw new Error("Hermes plugin registry is unavailable");
}

registry.register("evolution", EvolutionPlugin);
