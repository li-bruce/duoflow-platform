import { useMemo, useRef, useState, useCallback } from "react";
import { JsonForms } from "@jsonforms/react";
import { UPDATE_CORE, type Middleware } from "@jsonforms/core";
import { vanillaCells, vanillaRenderers } from "@jsonforms/vanilla-renderers";
import { post, requestId } from "../api";
import { ErrorNotice } from "../ui";
import { schemaValidator } from "../schemaValidator";
import type { Data } from "../types";
import type { SavedComposition, CompositionRun } from "./types";

/** Start is the only action here. Editing a field cannot call a business capability. */
export default function CompositionStart({
  definition,
  onStarted,
  conversationId,
}: {
  definition: SavedComposition;
  onStarted: (run: CompositionRun) => void;
  conversationId?: string;
}) {
  const [data, setData] = useState<Data>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const live = useRef({ data, invalid: true });
  const attempt = useRef<{ scope: string; key: string } | undefined>(undefined);
  const schema = useMemo(() => definition.document.input_schema, [definition]);
  const middleware = useCallback<Middleware>((state, action, reducer) => {
    const next = reducer(
      state,
      action.type === UPDATE_CORE ? { ...action, data: state.data } : action,
    );
    live.current = {
      data: next.data || {},
      invalid: Boolean(next.errors?.length),
    };
    return next;
  }, []);
  async function start() {
    if (live.current.invalid) {
      setError(new Error("请补齐输入内容后开始。"));
      return;
    }
    setBusy(true);
    setError(undefined);
    const input = live.current.data,
      scope = JSON.stringify([definition.id, input]);
    if (attempt.current?.scope !== scope)
      attempt.current = { scope, key: requestId() };
    try {
      const run = await post<CompositionRun>("/compositions/runs", {
        definition_id: definition.id,
        input,
        idempotency_key: attempt.current.key,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      });
      onStarted(run);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="composition-start">
      <h3>开始 {definition.document.title}</h3>
      <p>{definition.document.description}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <JsonForms
          schema={schema}
          data={data}
          renderers={vanillaRenderers}
          cells={vanillaCells}
          ajv={schemaValidator}
          middleware={middleware}
          onChange={(event) => setData(event.data || {})}
          readonly={busy}
        />
        <ErrorNotice error={error} />
        <button className="button primary" disabled={busy}>
          {busy ? "正在开始…" : "开始办理"}
        </button>
      </form>
    </section>
  );
}
