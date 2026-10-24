/** Server-side observation queue. Business handlers never wait for delivery. */
export function createObservationClient({
  callbackUrl,
  graph,
  fetchImpl = globalThis.fetch,
  timeoutMs = 1000,
}) {
  const endpoint = new URL(callbackUrl);
  if (
    !["http:", "https:"].includes(endpoint.protocol) ||
    endpoint.username ||
    endpoint.password ||
    endpoint.hash ||
    endpoint.search ||
    endpoint.pathname !== "/api/v1/runtime-observations/events"
  )
    throw new Error("Invalid configured observation endpoint");
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 2000)
    throw new Error(
      "Observation timeout must be between 1 and 2000 milliseconds",
    );
  const definition = structuredClone(graph);
  const active = new Set();
  let pending = 0;

  /** Keep a bounded queue and retry the same event identity at most once. */
  function deliver(ticket, batch, lost) {
    if (pending >= 64) {
      lost();
      return;
    }
    pending += 1;
    const task = (async () => {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          const response = await fetchImpl(endpoint, {
            method: "POST",
            redirect: "error",
            signal: AbortSignal.timeout(timeoutMs),
            headers: {
              "Content-Type": "application/json",
              "X-DuoFlow-Observation": ticket,
            },
            body: JSON.stringify(batch),
          });
          const ok = response.ok;
          await response.body?.cancel();
          if (ok) return;
          if (response.status < 500) break;
        } catch {
          /* Delivery problems never change a business result. */
        }
      }
      lost();
    })().finally(() => {
      pending -= 1;
      active.delete(task);
    });
    active.add(task);
  }

  return {
    /** Admission uses the configured destination and a one-time challenge. */
    async probe(challenge) {
      if (
        !challenge ||
        !/^[0-9a-f]{64}$/.test(challenge.ticket) ||
        !/^[0-9a-f]{32}$/.test(challenge.nonce)
      )
        return false;
      try {
        const url = new URL(endpoint);
        url.pathname = "/api/v1/runtime-observations/probe";
        const response = await fetchImpl(url, {
          method: "POST",
          redirect: "error",
          signal: AbortSignal.timeout(timeoutMs),
          headers: {
            "Content-Type": "application/json",
            "X-DuoFlow-Observation": challenge.ticket,
          },
          body: JSON.stringify({
            runtime_version: "1.0",
            nonce: challenge.nonce,
          }),
        });
        if (!response.ok) {
          await response.body?.cancel();
          return false;
        }
        const reader = response.body.getReader();
        let data = "",
          size = 0;
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 1024) {
            await reader.cancel();
            return false;
          }
          data += new TextDecoder().decode(value);
        }
        const result = JSON.parse(data);
        return result.accepted === true && result.nonce === challenge.nonce;
      } catch {
        return false;
      }
    },
    /** A request owns its sequence and correlation; tickets stay in memory. */
    session({ ticket, requestId, atomId }) {
      let sequence = 0,
        dropped = 0;
      const valid =
        typeof ticket === "string" &&
        /^[0-9a-f]{64}$/.test(ticket) &&
        /^[0-9a-f]{32}$/.test(requestId);
      const emit = (phase, state) => {
        if (!valid) return;
        if (sequence >= 256) {
          dropped = Math.min(256, dropped + 1);
          return;
        }
        const event = {
          sequence: ++sequence,
          atom_id: atomId,
          call_id: requestId,
          parent_call_id: null,
          phase,
        };
        if (state) event.state = structuredClone(state);
        deliver(
          ticket,
          {
            runtime_version: "1.0",
            request_id: requestId,
            graph_version: definition.version,
            dropped,
            events: [event],
          },
          () => {
            dropped = Math.min(256, dropped + 1);
          },
        );
      };
      return {
        mark: (phase) => {
          if (["started", "succeeded", "failed"].includes(phase)) emit(phase);
        },
        /** Call only after the module has committed or read this actual projection. */
        state(machineId, projection) {
          if (
            !projection?.object_ref ||
            !Number.isSafeInteger(projection.revision)
          )
            return;
          emit("state", {
            machine_id: machineId,
            value: projection.state,
            revision: projection.revision,
            object_ref: projection.object_ref,
          });
        },
        status: () => ({ sequence, dropped }),
      };
    },
    /** Optional shutdown/test hook; ordinary requests do not await it. */
    flush: () => Promise.allSettled([...active]),
  };
}
