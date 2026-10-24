import { useState } from "react";
import type { JsonSchema } from "@jsonforms/core";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { post, requestId } from "../api";
import { ErrorNotice } from "../ui";
import type { BusinessModule } from "../business/types";
import {
  schemaFields,
  type Binding,
  type CompositionDocument,
  type CompositionStep,
  type SavedComposition,
} from "./types";
import { CompositionRoleEditor } from "./CompositionRoles";

const blank = (): CompositionDocument => ({
  composition_version: "1.0",
  id: "composition." + requestId().slice(0, 8),
  version: "1.0.0",
  title: "",
  description: "",
  input_schema: {
    type: "object",
    properties: {},
    required: [],
    additionalProperties: false,
  },
  steps: [],
  roles: [],
});

export default function CompositionEditor({
  modules,
  initial,
  onSaved,
}: {
  modules: BusinessModule[];
  initial?: CompositionDocument;
  onSaved: (value: SavedComposition) => void;
}) {
  const [document, setDocument] = useState<CompositionDocument>(
    initial || blank,
  );
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const entries = modules.flatMap((module) =>
    module.flows.map((flow) => ({
      module,
      flow,
      key: `${module.module_id}/${flow.id}`,
    })),
  );
  const entryFor = (step: CompositionStep) =>
    entries.find(
      (entry) =>
        entry.module.module_id === step.module_id &&
        entry.flow.id === step.flow_id,
    )?.flow;

  /** New input fields take their exact type from the selected module interface. */
  function updateSteps(
    steps: CompositionStep[],
    extraInput: Record<string, JsonSchema> = {},
  ) {
    const properties: Record<string, JsonSchema> = {},
      required: string[] = [];
    for (const step of steps)
      for (const [field, binding] of Object.entries(step.bindings)) {
        if (binding.source !== "input") continue;
        const key = binding.path[0],
          flow = entryFor(step);
        properties[key] = extraInput[key] ||
          (document.input_schema.properties?.[key] as JsonSchema) || {
            ...(flow?.input_schema.properties?.[field] as JsonSchema),
            title: `${step.title} · ${field}`,
          };
        if (flow?.input_schema.required?.includes(field)) required.push(key);
      }
    const inputSchema = {
      type: "object",
      properties,
      required: [...new Set(required)],
      additionalProperties: false,
    } as JsonSchema;
    setDocument((current) => ({
      ...current,
      steps,
      input_schema: inputSchema,
    }));
    setMessage("");
  }
  function add() {
    const entry = entries.find((item) => item.key === choice);
    if (!entry || document.steps.length >= 20) return;
    const id = "step-" + requestId().slice(0, 8);
    const bindings: Record<string, Binding> = {};
    const fields: Record<string, JsonSchema> = {};
    for (const [key, value] of Object.entries(
      entry.flow.input_schema.properties || {},
    )) {
      if (!entry.flow.input_schema.required?.includes(key)) continue;
      const name = `${id}_${key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
      bindings[key] = { source: "input", path: [name] };
      fields[name] = {
        ...(value as JsonSchema),
        title: `${entry.flow.title} · ${(value as JsonSchema).title || key}`,
      };
    }
    updateSteps(
      [
        ...document.steps,
        {
          id,
          title: entry.flow.title,
          module_id: entry.module.module_id,
          flow_id: entry.flow.id,
          flow_version: entry.flow.version,
          bindings,
        },
      ],
      fields,
    );
  }
  function connect(step: CompositionStep, field: string, selected: string) {
    const bindings = { ...step.bindings };
    if (selected === "omit") delete bindings[field];
    else if (selected === "input")
      bindings[field] = {
        source: "input",
        path: [`${step.id}_${field.replace(/[^a-zA-Z0-9_-]/g, "_")}`],
      };
    else bindings[field] = JSON.parse(selected) as Binding;
    updateSteps(
      document.steps.map((item) =>
        item.id === step.id ? { ...item, bindings } : item,
      ),
    );
  }
  function move(index: number, direction: number) {
    const steps = [...document.steps];
    [steps[index], steps[index + direction]] = [
      steps[index + direction],
      steps[index],
    ];
    updateSteps(steps);
  }
  async function save(checkOnly = false) {
    setBusy(true);
    setError(undefined);
    setMessage("");
    try {
      if (checkOnly) {
        await post("/compositions/check", { document });
        setMessage(
          "输入、数据连接、模块版本和角色说明已检查通过。对象权限会在实际调用时继续检查。",
        );
      } else
        onSaved(
          await post<SavedComposition>("/compositions/definitions", {
            document,
          }),
        );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="composition-editor">
      <h2>把业务接成一个流程</h2>
      <p>
        先选择业务，再为每一步选择数据来源。需要人工办理的步骤会显示原模块的操作界面。
      </p>
      <div className="composition-names">
        <label className="field">
          组合名称
          <input
            value={document.title}
            maxLength={120}
            onChange={(event) =>
              setDocument({ ...document, title: event.target.value })
            }
            placeholder="为这组业务起一个名字"
          />
        </label>
        <label className="field">
          用途说明
          <textarea
            value={document.description}
            maxLength={1200}
            onChange={(event) =>
              setDocument({ ...document, description: event.target.value })
            }
            placeholder="说明这组业务要完成什么"
          />
        </label>
      </div>
      <div className="composition-add">
        <select
          aria-label="选择要加入的业务"
          value={choice}
          onChange={(event) => setChoice(event.target.value)}
        >
          <option value="">选择要加入的业务</option>
          {entries.map((entry) => (
            <option key={entry.key} value={entry.key}>
              {entry.module.title} · {entry.flow.title}
            </option>
          ))}
        </select>
        <button
          className="button secondary"
          onClick={add}
          disabled={!choice || document.steps.length >= 20}
        >
          <Plus size={16} />
          加入流程
        </button>
      </div>
      <ol className="composition-edit-steps">
        {document.steps.map((step, index) => {
          const flow = entryFor(step);
          return (
            <li key={step.id}>
              <header>
                <span className="composition-step-number">{index + 1}</span>
                <div>
                  <h3>{step.title}</h3>
                  <small>
                    {
                      modules.find(
                        (module) => module.module_id === step.module_id,
                      )?.title
                    }
                  </small>
                </div>
                <button
                  className="icon-button"
                  aria-label={`上移 ${step.title}`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label={`下移 ${step.title}`}
                  disabled={index === document.steps.length - 1}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label={`移除 ${step.title}`}
                  onClick={() =>
                    updateSteps(
                      document.steps.filter((item) => item.id !== step.id),
                    )
                  }
                >
                  <Trash2 size={16} />
                </button>
              </header>
              {!flow ? (
                <p>此业务当前不可用，请检查模块接入或权限。</p>
              ) : (
                <>
                  <p>{flow.description}</p>
                  {Object.entries(flow.input_schema.properties || {}).map(
                    ([field, raw]) => {
                      const schema = raw as JsonSchema,
                        binding = step.bindings[field];
                      const selected = !binding
                        ? "omit"
                        : binding.source === "input"
                          ? "input"
                          : JSON.stringify(binding);
                      const sources = document.steps
                        .slice(0, index)
                        .flatMap((previous) =>
                          schemaFields(
                            entryFor(previous)?.output_schema || {},
                          ).map((value) => ({
                            label: `${previous.title} → ${value.title}`,
                            binding: {
                              source: "step",
                              step_id: previous.id,
                              path: value.path,
                            } as Binding,
                          })),
                        );
                      const exists = sources.some(
                        (source) => JSON.stringify(source.binding) === selected,
                      );
                      return (
                        <label className="composition-binding" key={field}>
                          <span>
                            {schema.title || field}
                            {flow.input_schema.required?.includes(field)
                              ? " *"
                              : ""}
                          </span>
                          <select
                            aria-label={`${index + 1} ${schema.title || field}的数据来源`}
                            value={selected}
                            onChange={(event) =>
                              connect(step, field, event.target.value)
                            }
                          >
                            <option value="input">开始办理时填写</option>
                            {!flow.input_schema.required?.includes(field) && (
                              <option value="omit">使用模块默认值</option>
                            )}
                            {sources.map((source) => (
                              <option
                                key={JSON.stringify(source.binding)}
                                value={JSON.stringify(source.binding)}
                              >
                                {source.label}
                              </option>
                            ))}
                            {!exists &&
                              binding &&
                              binding.source !== "input" && (
                                <option value={selected}>
                                  {binding.source === "literal"
                                    ? "已保存的固定内容"
                                    : "来源步骤已移除或顺序变化"}
                                </option>
                              )}
                          </select>
                        </label>
                      );
                    },
                  )}
                  {!Object.keys(flow.input_schema.properties || {}).length && (
                    <small>此步骤从模块界面开始办理。</small>
                  )}
                  <details>
                    <summary>本步数据与权限</summary>
                    <p>
                      入口权限：
                      {flow.required_permissions.join("、") ||
                        "使用已登记的身份权限"}
                    </p>
                    <p>
                      数据操作：
                      {flow.data_access
                        .map(
                          (value) =>
                            `${value.object_id}：${value.operations.join("、")}`,
                        )
                        .join("；") || "使用本步输入与返回结果"}
                    </p>
                  </details>
                </>
              )}
            </li>
          );
        })}
      </ol>
      {!document.steps.length && (
        <div className="composition-empty">从上方选择第一项业务。</div>
      )}
      <CompositionRoleEditor
        modules={modules.filter((module) =>
          document.steps.some((step) => step.module_id === module.module_id),
        )}
        roles={document.roles}
        onChange={(roles) => setDocument({ ...document, roles })}
      />
      <details>
        <summary>组合编号与版本</summary>
        <div className="composition-names">
          <label className="field">
            编号
            <input
              value={document.id}
              onChange={(event) =>
                setDocument({ ...document, id: event.target.value })
              }
            />
          </label>
          <label className="field">
            版本
            <input
              value={document.version}
              onChange={(event) =>
                setDocument({ ...document, version: event.target.value })
              }
            />
          </label>
        </div>
      </details>
      <ErrorNotice error={error} />
      {message && (
        <p className="notice success" role="status">
          {message}
        </p>
      )}
      <div className="composition-actions">
        <button
          className="button secondary"
          disabled={busy || !document.steps.length}
          onClick={() => void save(true)}
        >
          检查连接
        </button>
        <button
          className="button primary"
          disabled={
            busy ||
            !document.steps.length ||
            !document.title ||
            !document.description
          }
          onClick={() => void save()}
        >
          保存组合
        </button>
      </div>
    </section>
  );
}
