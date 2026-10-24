import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { post, requestId } from "../api";
import { ErrorNotice } from "../ui";
import { isPlatformAdmin, type User } from "../types";
import type { BusinessModule } from "../business/types";
import type { CompositionRole, SavedComposition } from "./types";

const scopeTitle: Record<string, string> = {
  organization_id: "机构编号",
  project_id: "项目编号",
  team_id: "团队编号",
};

/** A role is a named group of explicit module roles, each retaining its own scope. */
export function CompositionRoleEditor({
  modules,
  roles,
  onChange,
}: {
  modules: BusinessModule[];
  roles: CompositionRole[];
  onChange: (roles: CompositionRole[]) => void;
}) {
  const [name, setName] = useState("");
  const options = modules.flatMap((module) =>
    [
      ...new Map(
        module.flows
          .flatMap((flow) => flow.role_templates || [])
          .map((role) => [role.id, role]),
      ).values(),
    ].map((role) => ({ module, role, key: `${module.module_id}/${role.id}` })),
  );
  function addBinding(index: number, key: string) {
    const chosen = options.find((option) => option.key === key);
    if (!chosen) return;
    onChange(
      roles.map((role, position) =>
        position === index
          ? {
              ...role,
              bindings: [
                ...role.bindings,
                {
                  module_id: chosen.module.module_id,
                  role_id: chosen.role.id,
                  scope: Object.fromEntries(
                    chosen.role.required_scope.map((field) => [field, ""]),
                  ),
                },
              ],
            }
          : role,
      ),
    );
  }
  return (
    <details className="composition-roles">
      <summary>安排组合中的角色</summary>
      <p>
        为一组职责起名，并逐项选择模块已有角色和适用范围。保存后由管理员分配给用户。
      </p>
      <div className="composition-add">
        <input
          aria-label="组合角色名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="角色名称，如办理人"
        />
        <button
          className="button secondary"
          disabled={!name.trim() || roles.length >= 20}
          onClick={() => {
            onChange([
              ...roles,
              {
                id: "role-" + requestId().slice(0, 8),
                title: name.trim(),
                bindings: [],
              },
            ]);
            setName("");
          }}
        >
          <Plus size={15} />
          添加角色
        </button>
      </div>
      {roles.map((role, index) => (
        <section key={role.id}>
          <header>
            <h4>{role.title}</h4>
            <button
              className="icon-button"
              aria-label={`删除角色 ${role.title}`}
              onClick={() =>
                onChange(roles.filter((_, position) => position !== index))
              }
            >
              <Trash2 size={15} />
            </button>
          </header>
          <select
            aria-label={`为 ${role.title} 添加模块角色`}
            value=""
            onChange={(event) => addBinding(index, event.target.value)}
          >
            <option value="">选择模块角色</option>
            {options.map((option) => (
              <option key={option.key} value={option.key}>
                {option.module.title} · {option.role.title}
              </option>
            ))}
          </select>
          {role.bindings.map((binding, bindingIndex) => (
            <div
              className="composition-role-binding"
              key={`${binding.module_id}/${binding.role_id}/${bindingIndex}`}
            >
              <strong>
                {options.find(
                  (option) =>
                    option.key === `${binding.module_id}/${binding.role_id}`,
                )?.role.title || binding.role_id}
              </strong>
              <small>{binding.module_id}</small>
              {Object.entries(binding.scope).map(([field, value]) => (
                <label className="field" key={field}>
                  {scopeTitle[field] || field}
                  <input
                    value={value}
                    onChange={(event) =>
                      onChange(
                        roles.map((entry, position) =>
                          position !== index
                            ? entry
                            : {
                                ...entry,
                                bindings: entry.bindings.map((old, offset) =>
                                  offset !== bindingIndex
                                    ? old
                                    : {
                                        ...old,
                                        scope: {
                                          ...old.scope,
                                          [field]: event.target.value,
                                        },
                                      },
                                ),
                              },
                        ),
                      )
                    }
                  />
                </label>
              ))}
              <button
                className="button secondary small"
                onClick={() =>
                  onChange(
                    roles.map((entry, position) =>
                      position !== index
                        ? entry
                        : {
                            ...entry,
                            bindings: entry.bindings.filter(
                              (_, offset) => offset !== bindingIndex,
                            ),
                          },
                    ),
                  )
                }
              >
                移除此项
              </button>
            </div>
          ))}
        </section>
      ))}
    </details>
  );
}

export function CompositionRoleAssignment({
  definition,
  user,
}: {
  definition: SavedComposition;
  user: User;
}) {
  const [subject, setSubject] = useState(""),
    [role, setRole] = useState("");
  const [error, setError] = useState<unknown>(),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const selected = definition.document.roles.find((item) => item.id === role);
  if (!definition.document.roles.length) return null;
  async function grant() {
    setBusy(true);
    setError(undefined);
    setMessage("");
    try {
      const result = await post<{ items: unknown[] }>(
        `/compositions/definitions/${definition.id}/roles/grant`,
        { role_id: role, subject_id: subject },
      );
      setMessage(
        `已确认 ${result.items.length} 项模块授权。可在业务授权中分别查看和撤销。`,
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="composition-roles">
      <summary>查看与分配组合角色</summary>
      {definition.document.roles.map((item) => (
        <section key={item.id}>
          <h4>{item.title}</h4>
          {item.bindings.map((binding, index) => (
            <p key={index}>
              {binding.module_id} · {binding.role_id} ·{" "}
              {Object.entries(binding.scope)
                .map(([key, value]) => `${scopeTitle[key] || key}：${value}`)
                .join("，")}
            </p>
          ))}
        </section>
      ))}
      {isPlatformAdmin(user) && (
        <>
          <label className="field">
            选择组合角色
            <select
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              <option value="">选择角色</option>
              {definition.document.roles.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            用户编号
            <input
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
            />
          </label>
          <p>确认后将按上方列出的范围授予该用户对应的模块权限。</p>
          <button
            className="button secondary"
            disabled={!selected || !subject || busy}
            onClick={() => void grant()}
          >
            确认分配角色
          </button>
        </>
      )}
      <ErrorNotice error={error} />
      {message && <p role="status">{message}</p>}
    </details>
  );
}
