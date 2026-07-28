(() => {
  // ../plugins/evolution/dashboard/src/sdk.ts
  function getSdk() {
    const hostSdk = window.__HERMES_PLUGIN_SDK__;
    if (hostSdk === void 0) {
      throw new Error("Hermes plugin SDK is unavailable");
    }
    const {
      Badge,
      Button,
      Checkbox,
      Input,
      Label,
      Select,
      SelectOption,
      Separator
    } = hostSdk.components;
    if (Badge === void 0 || Button === void 0 || Checkbox === void 0 || Input === void 0 || Label === void 0 || Select === void 0 || SelectOption === void 0 || Separator === void 0) {
      throw new Error("Hermes plugin UI components are unavailable");
    }
    return {
      React: hostSdk.React,
      hooks: hostSdk.hooks,
      fetchJSON: hostSdk.fetchJSON,
      components: { Badge, Button, Checkbox, Input, Label, Select, SelectOption, Separator },
      utils: hostSdk.utils
    };
  }
  var SDK = getSdk();
  var React = SDK.React;

  // ../plugins/evolution/dashboard/src/index.tsx
  function EvolutionPlugin() {
    return React.createElement("main", { className: "evo-shell" }, "Evolution");
  }
  var registry = window.__HERMES_PLUGINS__;
  if (registry === void 0) {
    throw new Error("Hermes plugin registry is unavailable");
  }
  registry.register("evolution", EvolutionPlugin);
})();
