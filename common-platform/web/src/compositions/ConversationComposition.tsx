import { lazy, Suspense, useEffect, useState } from "react";
import { api } from "../api";
import type { UIContext } from "../ResultView";
import { ErrorNotice, Loading } from "../ui";
import CompositionStart from "./CompositionStart";
import type { SavedComposition } from "./types";

const CompositionRunView = lazy(() => import("./CompositionRunView"));

/** The conversation stores a definition or run reference, never an executable plan in prose. */
export default function ConversationComposition({
  definitionId,
  context,
}: {
  definitionId: string;
  context: UIContext;
}) {
  const [definition, setDefinition] = useState<SavedComposition>();
  const [run, setRun] = useState("");
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    let active = true;
    void api<SavedComposition>(
      `/compositions/definitions/${encodeURIComponent(definitionId)}`,
    )
      .then((value) => {
        if (active) setDefinition(value);
      })
      .catch((failure) => {
        if (active) setError(failure);
      });
    return () => {
      active = false;
    };
  }, [definitionId]);
  return (
    <>
      <ErrorNotice error={error} />
      {run ? (
        <Suspense fallback={<Loading label="正在恢复办理记录…" />}>
          <CompositionRunView runId={run} context={context} />
        </Suspense>
      ) : definition ? (
        <CompositionStart
          definition={definition}
          conversationId={context.conversationId}
          onStarted={(value) => {
            setRun(value.id);
            context.onChanged();
          }}
        />
      ) : (
        !error && <Loading label="正在准备组合业务…" />
      )}
    </>
  );
}
