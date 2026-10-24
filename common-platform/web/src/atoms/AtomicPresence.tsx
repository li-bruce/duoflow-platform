import { useEffect, useRef, type ReactNode } from "react";
import { api } from "../api";

type Props = {
  kind: "workflow" | "interaction";
  instanceId: string;
  stepId?: string;
  revision?: number;
  surface: "page" | "conversation";
  status: "ready" | "editing" | "busy" | "error";
  children: ReactNode;
};

/** Report presentation status separately from saved drafts and formal state.
 * A failed observation never blocks editing, saving or invoking the component.
 */
export default function AtomicPresence({ children, ...props }: Props) {
  const session = useRef(crypto.randomUUID().replaceAll("-", ""));
  const latest = useRef(props);
  latest.current = props;
  const send = useRef<() => void>(() => {});

  useEffect(() => {
    let active = true,
      pending = false,
      registered = true;
    const publish = () => {
      const current = latest.current;
      if (!active || pending || !registered || current.revision === undefined)
        return;
      pending = true;
      void api<{ registered: boolean }>(
        `/atomic-instances/${current.kind}/${encodeURIComponent(current.instanceId)}/presence`,
        {
          method: "POST",
          signal: AbortSignal.timeout(2000),
          body: JSON.stringify({
            session_id: session.current,
            step_id: current.stepId ?? null,
            revision: current.revision,
            surface: current.surface,
            status: current.status,
          }),
        },
      )
        .then((result) => {
          if (result?.registered === false) registered = false;
        })
        .catch(() => {
          /* Freshness expires when delivery is unavailable. */
        })
        .finally(() => {
          pending = false;
        });
    };
    send.current = publish;
    publish();
    const timer = setInterval(publish, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [props.instanceId, props.kind, props.stepId]);

  useEffect(() => {
    send.current();
  }, [props.status, props.revision, props.surface]);
  return <>{children}</>;
}
