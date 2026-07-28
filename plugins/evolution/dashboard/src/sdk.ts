import type * as ReactTypes from "react";
import type { HermesPluginSDK } from "../../../../web/src/plugins/sdk";

export interface EvolutionPluginSdk {
  React: typeof ReactTypes;
  hooks: HermesPluginSDK["hooks"];
  fetchJSON: HermesPluginSDK["fetchJSON"];
  components: Pick<
    HermesPluginSDK["components"],
    | "Badge"
    | "Button"
    | "Checkbox"
    | "Input"
    | "Label"
    | "Select"
    | "SelectOption"
    | "Separator"
  >;
  utils: HermesPluginSDK["utils"];
}

function getSdk(): EvolutionPluginSdk {
  const hostSdk = window.__HERMES_PLUGIN_SDK__;
  if (hostSdk === undefined) {
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
    Separator,
  } = hostSdk.components;

  if (
    Badge === undefined ||
    Button === undefined ||
    Checkbox === undefined ||
    Input === undefined ||
    Label === undefined ||
    Select === undefined ||
    SelectOption === undefined ||
    Separator === undefined
  ) {
    throw new Error("Hermes plugin UI components are unavailable");
  }

  return {
    React: hostSdk.React,
    hooks: hostSdk.hooks,
    fetchJSON: hostSdk.fetchJSON,
    components: { Badge, Button, Checkbox, Input, Label, Select, SelectOption, Separator },
    utils: hostSdk.utils,
  };
}

export const SDK = getSdk();
export const React = SDK.React;
