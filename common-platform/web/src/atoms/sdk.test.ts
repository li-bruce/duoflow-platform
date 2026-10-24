import { describe, expect, it } from "vitest";
import { atomForHost, registerAtomicComponents } from "./sdk";
import type { FrontendAtom } from "./sdk";

const definition: FrontendAtom = {
  id: "example.editor",
  kind: "frontend",
  title: "填写",
  description: "共享填写组件",
  host: {
    kind: "workflow_step",
    id: "example.flow",
    version: "1.0.0",
    step_id: "edit",
  },
  input_schema: { type: "object" },
  output_schema: { type: "object" },
  state_schema: { type: "object" },
  initial_state: {},
  events: [],
};

describe("atomic component identities", () => {
  it("retains the exact component while isolating mutable contract copies", () => {
    const component = () => "shared";
    const registration = registerAtomicComponents([definition], {
      [definition.id]: component,
    });
    const found = registration.get(definition.id);
    expect(found.component).toBe(component);
    found.definition.title = "changed";
    expect(registration.get(definition.id).definition.title).toBe("填写");
  });
  it("rejects absent, extra and duplicate bindings", () => {
    expect(() => registerAtomicComponents([definition], {})).toThrow();
    expect(() => registerAtomicComponents([], { extra: () => null })).toThrow();
    expect(() =>
      registerAtomicComponents([definition, definition], {
        [definition.id]: () => null,
      }),
    ).toThrow();
  });
  it("matches host version and step, so reused components do not merge instances", () => {
    expect(atomForHost([definition], definition.host)?.id).toBe(definition.id);
    expect(
      atomForHost([definition], { ...definition.host, version: "2.0.0" }),
    ).toBeNull();
    expect(
      atomForHost([definition], { ...definition.host, step_id: "review" }),
    ).toBeNull();
  });
});
