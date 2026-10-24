import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, post, requestId, ApiError } from "../api";
import { ErrorNotice, Loading } from "../ui";
import ErrorBoundary from "../ErrorBoundary";
import { installedStep } from "./catalog";
import AtomicPresence from "../atoms/AtomicPresence";
import { fileSha256, Sha256 } from "./sha256";
import type {
  Data,
  DetailPage,
  ObjectProjection,
  WorkflowDefinition,
  WorkflowInstance,
  WorkflowStepProps,
} from "./sdk";
import "./workflows.css";

type HostProps = {
  instanceId: string;
  surface?: "page" | "conversation";
  onChanged?: () => void;
};
const accessLost = (error: unknown) =>
  error instanceof ApiError &&
  ([401, 403, 404].includes(error.status) ||
    [
      "authorization_changed",
      "flow_changed",
      "flow_unavailable",
      "access_authorization_changed",
      "access_policy_conflict",
    ].includes(error.code));
export default function FlowHost(props: HostProps) {
  return <FlowInstance key={props.instanceId} {...props} />;
}

function FlowInstance({ instanceId, surface = "page", onChanged }: HostProps) {
  const [item, setItem] = useState<WorkflowInstance>();
  const [definition, setDefinition] = useState<WorkflowDefinition>();
  const [drafts, setDrafts] = useState<Data>({});
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState("正在读取业务状态…");
  const [busy, setBusy] = useState(false);
  const [pageGroup, setPageGroup] = useState("");
  const draftValues = useRef<Data>({});
  const current = useRef<WorkflowInstance | undefined>(undefined);
  const desired = useRef<{ cursor: string; drafts: Data } | null>(null);
  const saving = useRef<Promise<void> | null>(null);
  const blocked = useRef(false);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const attempt = useRef<{ scope: string; key: string } | null>(null);
  const uploads = useRef(
    new Map<string, { key: string; revision: number; id?: string }>(),
  );
  const [reloading, setReloading] = useState(true);
  const reading = useRef(false);
  const working = useRef(false);
  const generation = useRef(0);
  const readSequence = useRef(0);
  const path = `/flows/instances/${instanceId}`;
  const active = useCallback(
    (version: number) => alive.current && generation.current === version,
    [],
  );
  const clearAccess = useCallback(
    (failure: unknown, version = generation.current) => {
      if (version !== generation.current || !accessLost(failure)) return false;
      // Fence all admitted reads/saves/commands before erasing private state.
      // In particular unmount must not flush a draft from a revoked instance.
      generation.current++;
      readSequence.current++;
      blocked.current = true;
      desired.current = null;
      current.current = undefined;
      draftValues.current = {};
      attempt.current = null;
      uploads.current.clear();
      reading.current = false;
      working.current = false;
      if (timer.current) clearTimeout(timer.current);
      timer.current = undefined;
      if (alive.current) {
        setDrafts({});
        setItem(undefined);
        setDefinition(undefined);
        setPageGroup("");
        setBusy(false);
        setReloading(false);
        setError(failure);
        setNotice("办理权限已变化，已清除当前业务内容，请重新打开。");
      }
      return true;
    },
    [],
  );
  const apply = useCallback(
    (
      record: WorkflowInstance,
      replaceDrafts = false,
      version = generation.current,
    ) => {
      if (!active(version) || record.id !== instanceId) return false;
      const before = current.current;
      if (
        before &&
        (record.revision < before.revision ||
          (JSON.stringify(record.object_ref) ===
            JSON.stringify(before.object_ref) &&
            record.projection &&
            before.projection &&
            record.projection.revision < before.projection.revision))
      )
        return false;
      current.current = record;
      setItem(record);
      if (replaceDrafts) {
        draftValues.current = record.drafts;
        setDrafts(record.drafts);
      }
      return true;
    },
    [active, instanceId],
  );
  const refresh = useCallback(async () => {
    if (!alive.current || reading.current) return;
    const version = generation.current,
      sequence = ++readSequence.current,
      before = current.current;
    try {
      const record = await api<WorkflowInstance>(path);
      if (
        active(version) &&
        sequence === readSequence.current &&
        !reading.current &&
        current.current === before &&
        apply(record, false, version) &&
        before &&
        ["queued", "running"].includes(before.status) &&
        !["queued", "running"].includes(record.status)
      )
        setNotice("已取得后台回执，业务状态已更新");
    } catch (e) {
      if (
        active(version) &&
        sequence === readSequence.current &&
        !reading.current &&
        current.current === before
      ) {
        clearAccess(e, version);
        throw e;
      }
    }
  }, [path, apply, active, clearAccess]);
  const reload = useCallback(async () => {
    if (reading.current || working.current) return;
    const version = ++generation.current;
    reading.current = true;
    setReloading(true);
    setError(undefined);
    if (timer.current) clearTimeout(timer.current);
    try {
      if (saving.current) await saving.current.catch(() => {});
      if (!active(version)) return;
      const [record, catalog] = await Promise.all([
        api<WorkflowInstance>(path),
        api<{ items: WorkflowDefinition[] }>("/flows/catalog"),
      ]);
      if (!active(version)) return;
      const flow = catalog.items.find(
        (f) => f.id === record.flow_id && f.version === record.flow_version,
      );
      if (!flow || flow.available === false)
        throw new ApiError(
          "flow_unavailable",
          "流程定义不可用，请重新打开。",
          404,
        );
      if (!apply(record, true, version))
        throw new Error("过期的流程响应已忽略，请重新读取。");
      desired.current = null;
      blocked.current = false;
      attempt.current = null;
      setDefinition(flow);
      setNotice("已读取最新业务状态");
    } catch (e) {
      if (active(version) && !clearAccess(e, version)) setError(e);
    } finally {
      if (active(version)) {
        reading.current = false;
        setReloading(false);
      }
    }
  }, [path, apply, active, clearAccess]);
  const flush = useCallback(async () => {
    const version = generation.current;
    while (saving.current) await saving.current;
    if (version !== generation.current || reading.current) return;
    if (blocked.current) throw new Error("个人草稿未保存，请重新读取后继续。");
    const job = (async () => {
      while (
        version === generation.current &&
        desired.current &&
        current.current
      ) {
        const next = desired.current;
        try {
          const record = await api<WorkflowInstance>(path, {
            method: "PATCH",
            body: JSON.stringify({
              ...next,
              expected_revision: current.current.revision,
            }),
          });
          if (version !== generation.current) return;
          // This keyed instance may finish its own last autosave after leaving;
          // it can never update the next instance or publish an old read there.
          if (alive.current) {
            if (!apply(record, false, version))
              throw new Error(
                "过期的保存响应已忽略，请重新读取；本窗口草稿保留。",
              );
          } else current.current = record;
          if (desired.current === next) desired.current = null;
          if (alive.current)
            setNotice(desired.current ? "正在保存个人草稿…" : "个人草稿已保存");
        } catch (e) {
          if (version === generation.current && !clearAccess(e, version)) {
            blocked.current = true;
            if (alive.current) {
              setError(e);
              setNotice("保存发生冲突，请重新读取");
            }
          }
          throw e;
        }
      }
    })();
    saving.current = job;
    try {
      await job;
    } finally {
      if (saving.current === job) saving.current = null;
    }
  }, [path, apply, clearAccess]);
  useEffect(() => {
    alive.current = true;
    void reload();
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
      void flush().catch(() => {});
    };
  }, [reload, flush]);
  const pending = !!item && ["queued", "running"].includes(item.status);
  const jobPending = (
    item?.projection?.jobs ||
    item?.projection?.data.jobs ||
    []
  ).some((j: Data) =>
    ["queued", "running", "cancelling"].includes(j.state || j.status),
  );
  useEffect(() => {
    if (!pending && !jobPending) return;
    const poll = setInterval(() => {
      if (
        !desired.current &&
        !saving.current &&
        !working.current &&
        !reading.current
      )
        void refresh().catch(setError);
    }, 1400);
    return () => clearInterval(poll);
  }, [pending, jobPending, refresh]);
  useEffect(() => {
    const poll = setInterval(() => {
      if (
        current.current &&
        !saving.current &&
        !working.current &&
        !reading.current
      )
        void refresh().catch(setError);
    }, 15000);
    return () => clearInterval(poll);
  }, [refresh]);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (desired.current || saving.current || busy) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [busy]);
  function updateDraft(stepId: string, draft: Data) {
    if (
      !alive.current ||
      !current.current ||
      working.current ||
      reading.current ||
      ["queued", "running"].includes(current.current.status) ||
      blocked.current
    )
      return;
    const cursor = current.current.cursor;
    const next = { ...draftValues.current, [stepId]: draft };
    draftValues.current = next;
    setDrafts(next);
    desired.current = { cursor, drafts: next };
    attempt.current = null;
    setNotice("有未提交的个人草稿");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush().catch(() => {}), 300);
  }
  function beginWork() {
    if (
      !alive.current ||
      working.current ||
      reading.current ||
      ["queued", "running"].includes(current.current?.status || "")
    )
      throw new Error("当前有操作尚未结束。");
    working.current = true;
    setBusy(true);
    return generation.current;
  }
  function finishWork(version: number) {
    if (active(version)) {
      working.current = false;
      setBusy(false);
    }
  }
  function stillHere(version: number) {
    if (!active(version)) throw new Error("流程视图已切换，请在当前视图继续。");
  }
  async function moveCursor(stepId: string) {
    await flush();
    if (!current.current) return;
    if (current.current.cursor !== stepId) {
      desired.current = { cursor: stepId, drafts: draftValues.current };
      await flush();
    }
  }
  async function navigate(stepId: string, groupId?: string) {
    let version: number | undefined;
    try {
      version = beginWork();
      const group = definition?.page_groups?.find((g) =>
        groupId ? g.id === groupId : g.steps.includes(stepId),
      );
      if (group) setPageGroup(group.id);
      await moveCursor(stepId);
    } catch (e) {
      if (alive.current) setError(e);
    } finally {
      if (version !== undefined) finishWork(version);
    }
  }
  async function openObject(objectId: string) {
    const version = beginWork();
    try {
      await flush();
      stillHere(version);
      if (!current.current) return;
      apply(
        await post<WorkflowInstance>(path + "/select", {
          object_id: objectId,
          expected_revision: current.current.revision,
        }),
        true,
        version,
      );
      attempt.current = null;
    } catch (e) {
      clearAccess(e, version);
      throw e;
    } finally {
      finishWork(version);
    }
  }
  async function execute(stepId: string, actionId: string, command: Data) {
    const version = beginWork();
    setError(undefined);
    try {
      await moveCursor(stepId);
      stillHere(version);
      if (!current.current) throw new Error("请先恢复流程。");
      const body = {
        command,
        expected_revision: current.current.revision,
        expected_object_revision: current.current.projection?.revision ?? null,
      };
      const scope = JSON.stringify([actionId, body]);
      if (attempt.current?.scope !== scope)
        attempt.current = { scope, key: requestId() };
      const response = await post<{ instance: WorkflowInstance }>(
        path + "/actions/" + encodeURIComponent(actionId),
        { ...body, idempotency_key: attempt.current.key },
      );
      stillHere(version);
      attempt.current = null;
      apply(response.instance, true, version);
      setNotice(
        response.instance.status === "queued"
          ? "命令已排队，等待真实回执"
          : "命令已完成",
      );
      onChanged?.();
      return response;
    } catch (e) {
      if (active(version) && !clearAccess(e, version)) {
        setError(e);
        if (
          e instanceof ApiError &&
          e.status === 409 &&
          ["revision_conflict", "state_conflict"].includes(e.code)
        ) {
          blocked.current = true;
          setNotice("业务记录已变化，请重新读取；本窗口草稿保留");
        } else setNotice("命令结果未确认；可重试原请求或刷新查询");
      }
      throw e;
    } finally {
      finishWork(version);
    }
  }
  async function retryRun() {
    if (!current.current?.run_id) return;
    const version = beginWork();
    setError(undefined);
    try {
      await flush();
      stillHere(version);
      await post(`/runs/${encodeURIComponent(current.current!.run_id!)}/retry`);
      stillHere(version);
      await refresh();
      stillHere(version);
      setNotice("正在重试原请求，使用原有输入与幂等标识");
    } catch (e) {
      if (active(version) && !clearAccess(e, version)) setError(e);
    } finally {
      finishWork(version);
    }
  }
  const revision = () => {
    const value = current.current?.projection?.revision;
    if (!value) throw new Error("请先选择业务对象。");
    return value;
  };
  async function transfer(
    file: File,
    id: string | undefined,
    onProgress?: (received: number, total: number) => void,
  ) {
    const version = beginWork();
    setError(undefined);
    try {
      await flush();
      stillHere(version);
      setNotice("正在计算文件完整性摘要…");
      const sha256 = await fileSha256(file);
      stillHere(version);
      const scope = JSON.stringify([
        current.current?.object_ref,
        file.name,
        file.size,
        sha256,
      ]);
      if (!uploads.current.has(scope))
        uploads.current.set(scope, {
          key: requestId(),
          revision: revision(),
          id,
        });
      const uploadAttempt = uploads.current.get(scope)!;
      const existingId = id || uploadAttempt.id;
      let response = existingId
        ? await api<Data>(path + "/uploads/" + encodeURIComponent(existingId))
        : await post<Data>(path + "/uploads", {
            filename: file.name,
            size: file.size,
            sha256,
            expected_revision: uploadAttempt.revision,
            idempotency_key: uploadAttempt.key,
          });
      const upload = response.upload;
      if (upload.sha256 !== sha256 || upload.size !== file.size)
        throw new Error("选择的文件与上传会话不匹配。");
      const uploadId = upload.upload_id;
      if (typeof uploadId !== "string") throw new Error("上传会话编号无效。");
      uploadAttempt.id = uploadId;
      // Creating a session may advance the module's revision. Read the current
      // authority before completion; chunk progress does not invent revisions.
      await refresh();
      stillHere(version);
      const chunkSize = Number(upload.chunk_size || 1024 * 1024);
      if (chunkSize < 1 || chunkSize > 1024 * 1024)
        throw new Error("上传分块大小无效。");
      const uploaded = new Set<number>(upload.uploaded_chunks || []);
      let received = Array.from(uploaded).reduce(
        (n, index) => n + Math.min(chunkSize, file.size - index * chunkSize),
        0,
      );
      onProgress?.(received, file.size);
      for (let index = 0; index < Math.ceil(file.size / chunkSize); index++) {
        if (uploaded.has(index)) continue;
        const bytes = new Uint8Array(
          await file
            .slice(index * chunkSize, (index + 1) * chunkSize)
            .arrayBuffer(),
        );
        setNotice("正在上传文件分块…");
        stillHere(version);
        response = await api<Data>(
          `${path}/uploads/${encodeURIComponent(uploadId)}/chunks/${index}`,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-Chunk-SHA256": new Sha256().update(bytes).hex(),
            },
            body: bytes,
          },
        );
        stillHere(version);
        received += bytes.byteLength;
        onProgress?.(received, file.size);
      }
      setNotice("正在校验完整文件并关联业务对象…");
      response = await post<Data>(
        `${path}/uploads/${encodeURIComponent(uploadId)}/complete`,
        { expected_revision: revision(), idempotency_key: uploadAttempt.key },
      );
      stillHere(version);
      await refresh();
      stillHere(version);
      uploads.current.delete(scope);
      setNotice("文件已上传并关联");
      return response;
    } catch (e) {
      if (active(version) && !clearAccess(e, version)) {
        setError(e);
        setNotice("上传尚未确认完成，可刷新并恢复上传");
      }
      throw e;
    } finally {
      finishWork(version);
    }
  }
  async function abortUpload(id: string) {
    const version = beginWork();
    try {
      await flush();
      stillHere(version);
      await post(`${path}/uploads/${encodeURIComponent(id)}/abort`, {
        expected_revision: revision(),
      });
      stillHere(version);
      for (const [key, value] of uploads.current)
        if (value.id === id) uploads.current.delete(key);
      await refresh();
    } catch (e) {
      clearAccess(e, version);
      throw e;
    } finally {
      finishWork(version);
    }
  }
  async function jobControl(
    id: string,
    operation: "status" | "result" | "cancel",
  ) {
    const version = beginWork();
    try {
      await flush();
      stillHere(version);
      const url = `${path}/jobs/${encodeURIComponent(id)}`;
      const result =
        operation === "cancel"
          ? await post(url + "/cancel", {
              expected_revision: revision(),
              idempotency_key: requestId(),
            })
          : await api(url + (operation === "result" ? "/result" : ""));
      stillHere(version);
      await refresh();
      return result;
    } catch (e) {
      clearAccess(e, version);
      throw e;
    } finally {
      finishWork(version);
    }
  }
  async function continueChat() {
    let version: number | undefined;
    try {
      version = beginWork();
      await flush();
      stillHere(version);
      const response = await post<{ conversation_id: string }>(
        path + "/attach",
      );
      stillHere(version);
      const state = await api<{ revision: number; drafts: Data }>(
        "/conversations/state",
      );
      stillHere(version);
      await api("/conversations/state", {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: state.revision,
          current_conversation_id: response.conversation_id,
          drafts: state.drafts,
        }),
      });
      stillHere(version);
      location.hash = "chat";
    } catch (e) {
      if (alive.current && !clearAccess(e, version)) setError(e);
    } finally {
      if (version !== undefined) finishWork(version);
    }
  }
  async function loadMoreObjects(objectType: string) {
    if (reading.current || working.current)
      throw new Error("当前有操作尚未结束。");
    const version = generation.current;
    const before = current.current;
    const cursor = before?.object_pages?.find(
      (p) => p.object_type === objectType,
    )?.next_cursor;
    if (!before || !cursor) return;
    let result: { objects: ObjectProjection[]; next_cursor: string | null };
    try {
      result = await post(path + "/objects/list", {
        object_type: objectType,
        cursor,
        limit: 50,
      });
    } catch (e) {
      clearAccess(e, version);
      throw e;
    }
    if (!active(version) || current.current !== before)
      throw new Error("业务目录已更新，请重新读取后翻页。");
    const combined = new Map(
      before.objects.map((o) => [JSON.stringify(o.object_ref), o]),
    );
    result.objects.forEach((o) =>
      combined.set(JSON.stringify(o.object_ref), o),
    );
    apply(
      {
        ...before,
        objects: [...combined.values()],
        object_pages: before.object_pages!.map((p) =>
          p.object_type === objectType
            ? { ...p, next_cursor: result.next_cursor }
            : p,
        ),
      },
      false,
      version,
    );
  }
  async function detailPage(section: string, cursor?: string, limit = 30) {
    if (reading.current || working.current)
      throw new Error("当前有操作尚未结束。");
    const version = generation.current;
    const before = current.current;
    if (!before?.object_ref || !before.projection)
      throw new Error("请先选择业务对象。");
    let result: DetailPage;
    try {
      result = await post(path + "/detail", {
        section,
        ...(cursor ? { cursor } : {}),
        limit,
      });
    } catch (e) {
      clearAccess(e, version);
      throw e;
    }
    if (
      !active(version) ||
      JSON.stringify(current.current?.object_ref) !==
        JSON.stringify(before.object_ref) ||
      current.current?.projection?.revision !== before.projection.revision ||
      result.revision !== before.projection.revision ||
      JSON.stringify(result.object_ref) !== JSON.stringify(before.object_ref)
    )
      throw new Error("业务对象或修订已变化，请重新读取详情。");
    return result;
  }
  const step = definition?.steps.find((s) => s.id === item?.cursor);
  const Component =
    definition && step ? installedStep(definition, step.component) : null;
  const propsFor = (stepId: string): WorkflowStepProps => ({
    surface,
    projection: item?.projection || null,
    objects: item?.objects || [],
    draft: drafts[stepId] || {},
    updateDraft: (draft) => updateDraft(stepId, draft),
    busy: busy || reloading || pending || blocked.current,
    actions:
      item?.step_actions?.[stepId] ||
      (stepId === item?.cursor ? item.actions : []) ||
      [],
    execute: (action, command) => execute(stepId, action, command),
    refresh,
    openObject,
    navigate: (id) => void navigate(id),
    objectPages: item?.object_pages || [],
    loadMoreObjects,
    detailPage,
    uploadFile: (file, progress) => transfer(file, undefined, progress),
    uploadSessions:
      item?.projection?.data.uploads ||
      item?.projection?.data.upload_sessions ||
      [],
    resumeUpload: (id, file, progress) => transfer(file, id, progress),
    abortUpload,
    jobControl,
  });
  const groups = definition?.page_groups;
  const group =
    groups?.find((g) => g.id === pageGroup) ||
    groups?.find((g) => g.steps.includes(item?.cursor || "")) ||
    groups?.[0];
  const renderStep = (id: string) => {
    const entry = definition?.steps.find((s) => s.id === id);
    const Content =
      definition && entry ? installedStep(definition, entry.component) : null;
    return Content && entry ? (
      <div key={id} className="flow-composed-section" data-flow-step={id}>
        <ErrorBoundary resetKey={instanceId + id}>
          <Suspense fallback={<Loading label="正在加载工作区…" />}>
            <fieldset
              disabled={busy || reloading || pending || blocked.current}
              style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}
            >
              <AtomicPresence
                kind="workflow"
                instanceId={instanceId}
                stepId={id}
                revision={item?.revision}
                surface={surface}
                status={
                  error || item?.error
                    ? "error"
                    : busy || pending
                      ? "busy"
                      : desired.current
                        ? "editing"
                        : "ready"
                }
              >
                <Content {...propsFor(id)} />
              </AtomicPresence>
            </fieldset>
          </Suspense>
        </ErrorBoundary>
      </div>
    ) : null;
  };
  return (
    <section
      className={"flow-host flow-" + surface}
      aria-label={item?.title || "步骤工作台"}
      data-flow-instance-id={instanceId}
    >
      <header className="flow-heading">
        <div>
          <small>{surface === "page" ? "步骤工作台" : "当前办理步骤"}</small>
          <h2>{item?.title || "正在恢复流程"}</h2>
          <p role="status">{notice}</p>
        </div>
        <div className="flow-controls">
          <button
            disabled={busy || reloading || pending}
            onClick={() => void reload()}
          >
            重新读取
          </button>
          {surface === "page" && (
            <button
              disabled={busy || reloading || pending}
              onClick={() => void continueChat()}
            >
              在对话中继续
            </button>
          )}
        </div>
      </header>
      <ErrorNotice error={error || item?.error} />
      {item?.run_id && item.status === "failed" && (
        <div className="flow-controls">
          <button
            disabled={busy || reloading || blocked.current}
            onClick={() => void retryRun()}
          >
            重试原请求
          </button>
          <small>继续原任务，使用原有输入；修改后的草稿不会替换原请求。</small>
        </div>
      )}
      {definition && item && step && Component ? (
        <div className="flow-layout">
          {surface === "page" ? (
            <nav aria-label={groups ? "工作台区域" : "流程步骤"}>
              {groups
                ? groups.map((g) => (
                    <button
                      key={g.id}
                      className={g.id === group?.id ? "active" : ""}
                      disabled={busy || reloading || pending}
                      onClick={() => void navigate(g.steps[0], g.id)}
                    >
                      {g.title}
                    </button>
                  ))
                : definition.steps.map((s) => (
                    <button
                      key={s.id}
                      className={s.id === item.cursor ? "active" : ""}
                      disabled={busy || reloading || pending}
                      onClick={() => void navigate(s.id)}
                    >
                      {s.title}
                    </button>
                  ))}
            </nav>
          ) : (
            <label className="flow-step-select">
              继续办理
              <select
                aria-label="当前步骤"
                value={item.cursor}
                disabled={busy || reloading || pending}
                onChange={(e) => void navigate(e.target.value)}
              >
                {definition.steps.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </label>
          )}
          <main>
            {surface === "page" && group ? (
              <>
                <div className="flow-guidance">
                  <h3>{group.title}</h3>
                  <p>{definition.description}</p>
                </div>
                <div className="flow-composition">
                  {group.steps.map(renderStep)}
                </div>
              </>
            ) : (
              <>
                <div className="flow-guidance">
                  <h3>{step.title}</h3>
                  <p>{step.description}</p>
                </div>
                {renderStep(step.id)}
              </>
            )}
          </main>
        </div>
      ) : (
        !error && <Loading label="正在读取流程…" />
      )}
      <footer className="flow-meta">
        个人流程 {instanceId.slice(0, 8)} · 草稿修订 {item?.revision || "—"}
        {item?.projection && <> · 业务修订 {item.projection.revision}</>}
        {item?.forked_from && " · 分支仅复制个人草稿，继续引用同一业务对象"}
      </footer>
    </section>
  );
}
