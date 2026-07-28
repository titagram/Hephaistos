import { afterEach, describe, expect, it, vi } from "vitest";

interface RegisteredPlugin {
  name: string;
  component: () => unknown;
}

interface RenderedElement {
  props: {
    children?: unknown;
    className?: unknown;
  };
  type: unknown;
}

interface TestReact {
  createElement(
    type: unknown,
    props: Record<string, unknown> | null,
    ...children: string[]
  ): {
    children: string[];
    props: Record<string, unknown> | null;
    type: unknown;
  };
}

type TestComponent = () => null;

interface TestPluginWindow {
  __HERMES_PLUGIN_SDK__?: {
    React: TestReact;
    hooks: object;
    fetchJSON<T>(path: string): Promise<T>;
    components: {
      Badge: TestComponent;
      Button: TestComponent;
      Checkbox: TestComponent;
      Input: TestComponent;
      Label: TestComponent;
      Select: TestComponent;
      SelectOption: TestComponent;
      Separator: TestComponent;
    };
    utils: {
      cn(...values: unknown[]): string;
      timeAgo(value: string): string;
      isoTimeAgo(value: string): string;
    };
  };
  __HERMES_PLUGINS__?: {
    register(name: string, component: () => unknown): void;
  };
}

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

afterEach(() => {
  if (originalWindow === undefined) {
    Reflect.deleteProperty(globalThis, "window");
  } else {
    Object.defineProperty(globalThis, "window", originalWindow);
  }
  vi.resetModules();
});

describe("Evolution dashboard plugin", () => {
  it("registers the host-rendered Evolution shell", async () => {
    let registered: RegisteredPlugin | undefined;
    const testWindow: TestPluginWindow = {
      __HERMES_PLUGIN_SDK__: {
        React: {
          createElement(type, props, ...children) {
            return {
              type,
              props: props === null ? null : { ...props, children: children.at(0) ?? "" },
              children,
            };
          },
        },
        hooks: {},
        async fetchJSON<T>(): Promise<T> {
          throw new Error("not used by registration");
        },
        components: {
          Badge: () => null,
          Button: () => null,
          Checkbox: () => null,
          Input: () => null,
          Label: () => null,
          Select: () => null,
          SelectOption: () => null,
          Separator: () => null,
        },
        utils: {
          cn: (...values) => values.filter(Boolean).join(" "),
          timeAgo: value => value,
          isoTimeAgo: value => value,
        },
      },
      __HERMES_PLUGINS__: {
        register(name, component) {
          registered = { name, component };
        },
      },
    };
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: testWindow,
    });

    await import("../../../plugins/evolution/dashboard/src/index.tsx");

    expect(registered?.name).toBe("evolution");
    const rendered = registered?.component() as RenderedElement;
    expect(typeof rendered.type).toBe("function");
    expect((rendered.type as Function).name).toBe("EvolutionShell");
  });
});
