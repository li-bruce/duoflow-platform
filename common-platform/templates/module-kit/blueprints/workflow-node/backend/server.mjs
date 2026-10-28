import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createModuleServer, requireThat } from "../sdk/node/runtime.mjs";
import { createAccessClient } from "../sdk/node/access-client.mjs";
import { createObservationClient } from "../sdk/node/observation.mjs";
import { AIServiceManager } from "../sdk/node/ai-service.mjs";
import { createTelemetryReporter } from "../sdk/node/telemetry.mjs";
import { CoordinationClient } from "../sdk/node/coordination.mjs";
import { createBusiness } from "./business.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
export const manifest = JSON.parse(
  readFileSync(path.join(root, "manifest.json"), "utf8"),
);
export const registration = JSON.parse(
  readFileSync(path.join(root, "registration.json"), "utf8"),
);
export const moduleGraph = JSON.parse(
  readFileSync(path.join(root, "module-graph.json"), "utf8"),
);
export function createService({
  token,
  callbackUrl,
  filename,
  projects,
  fetchImpl,
  observationUrl,
  aiManager = null,
  telemetryConfig = null,
  coordinationConfig = null,
  coordinationHandler = null,
  registrationDocument = registration,
}) {
  const declaration = registrationDocument.coordination;
  const coordinationEnabled = declaration &&
    (declaration.machines.length || declaration.emits.length || declaration.accepts.length);
  let coordination = null;
  if (coordinationConfig && coordinationEnabled) {
    requireThat(!coordinationConfig.module_id || coordinationConfig.module_id === manifest.module.id,
      "coordination_config", "协同凭据不属于当前模块。", 500);
    const endpoint = new URL(coordinationConfig.url);
    // The platform CLI exports a center host URL; SDKs use the protocol prefix.
    if (endpoint.pathname === "/") endpoint.pathname = "/api/v1/coordination";
    const intervalSeconds = coordinationConfig.interval_seconds ?? 15;
    requireThat(Number.isFinite(intervalSeconds) && intervalSeconds >= 1 && intervalSeconds <= 300,
      "coordination_config", "协同收发周期无效。", 500);
    coordination = {
      declaration,
      handler: coordinationHandler,
      intervalSeconds,
      client: new CoordinationClient({
        baseUrl: endpoint.href, token: coordinationConfig.token,
        moduleId: manifest.module.id,
        consumerId: coordinationConfig.consumer_id || manifest.module.id + ".worker",
        scopes: [...new Map(projects.map(project => {
          const scope = {organization_id:project.organization_id, project_id:project.project_id};
          return [JSON.stringify(scope), scope];
        })).values()],
        acceptedSources: [...new Set([manifest.module.id, ...declaration.accepts.map(item => item.source_module)])],
        timeoutSeconds: coordinationConfig.timeout_seconds ?? 3,
        fetchImpl,
      }),
    };
  }
  const business = createBusiness({
    filename,
    projects,
    decide: createAccessClient({ callbackUrl, fetchImpl }),
    ai: aiManager,
    coordination,
  });
  try {
    const observer = createObservationClient({
      callbackUrl:
        observationUrl ||
        new URL("/api/v1/runtime-observations/events", callbackUrl).href,
      graph: moduleGraph,
      fetchImpl,
    });
    // Observe only the projection returned after the business transaction. The
    // module names its own formal machine; the transport does not infer one.
    const handlers = Object.fromEntries(
      Object.entries(business.handlers).map(([id, handler]) => [
        id,
        async (context) => {
          const result = await handler(context);
          if (result.data.projection)
            context.observe?.state(
              "__MODULE_ID__.record-lifecycle",
              result.data.projection,
            );
          return result;
        },
      ]),
    );
    const server = createModuleServer({
      manifest,
      registration: registrationDocument,
      token,
      handlers,
      workflowHandlers: business.workflowHandlers,
      graph: moduleGraph,
      observationClient: observer,
      aiManager,
    });
    const counters = {inflight_requests:0, requests_total:0, errors_total:0};
    const telemetry = telemetryConfig ? createTelemetryReporter({
      ...telemetryConfig, metrics:()=>({...counters}),
    }) : null;
    if (telemetry) {
      server.on('request', (_request, response)=>{
        counters.requests_total++; counters.inflight_requests++;
        let counted=false;
        const finish=()=>{ if(counted)return; counted=true; counters.inflight_requests--;
          if(response.statusCode>=500 || !response.writableFinished)counters.errors_total++; };
        response.once('finish',finish); response.once('close',finish);
      });
      server.once('listening',()=>telemetry.start());
      server.once('close',()=>void telemetry.stop());
    }
    server.once('listening', () => business.coordination.start());
    server.once('close', () => void business.coordination.stop());
    return {
      server,
      coordination: business.coordination,
      close: async () => {
        if (server.listening)
          await new Promise((resolve) => server.close(resolve));
        await telemetry?.stop();
        await business.close();
        await observer?.flush();
      },
    };
  } catch (error) {
    void business.close();
    throw error;
  }
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
) {
  requireThat(
    process.env.MODULE_PROJECTS_FILE && process.env.SERVICE_DATA_DIR,
    "service_config",
    "请配置 MODULE_PROJECTS_FILE 和 SERVICE_DATA_DIR。",
    500,
  );
  const directory = path.resolve(process.env.SERVICE_DATA_DIR);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const projects = JSON.parse(
    readFileSync(path.resolve(process.env.MODULE_PROJECTS_FILE), "utf8"),
  );
  const service = createService({
    token: process.env.PLATFORM_SERVICE_TOKEN,
    callbackUrl: process.env.PLATFORM_ACCESS_DECISION_URL,
    observationUrl: process.env.PLATFORM_OBSERVATION_URL,
    filename: path.join(directory, "records.sqlite"),
    projects,
    coordinationConfig: process.env.DUOFLOW_COORDINATION_CONFIG_FILE
      ? JSON.parse(readFileSync(process.env.DUOFLOW_COORDINATION_CONFIG_FILE, 'utf8')) : null,
    telemetryConfig: process.env.DUOFLOW_MONITOR_CONFIG_FILE ? (()=>{
      const configuration=JSON.parse(readFileSync(process.env.DUOFLOW_MONITOR_CONFIG_FILE,'utf8'));
      return {url:configuration.url, token:configuration.token, intervalSeconds:configuration.interval_seconds ?? 15,
        ...(configuration.instance_id ? {instanceId:configuration.instance_id} : {})};
    })() : null,
    aiManager: registration.ai_services?.length ? new AIServiceManager({
      services: registration.ai_services,
      dataDir: path.join(directory, "ai"),
      platformBaseUrl: process.env.PLATFORM_AI_BASE_URL ?? null,
    }) : null,
  });
  const port = Number(process.env.PORT || 0);
  requireThat(
    Number.isInteger(port) && port >= 0 && port <= 65535,
    "service_config",
    "PORT 无效。",
    500,
  );
  service.server.listen(port, "127.0.0.1", () =>
    console.log(
      JSON.stringify({ ready: true, port: service.server.address().port }),
    ),
  );
  for (const signal of ["SIGTERM", "SIGINT"])
    process.on(signal, () => void service.close().then(() => process.exit(0)));
}
