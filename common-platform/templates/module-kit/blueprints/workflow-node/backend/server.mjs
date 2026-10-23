import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createModuleServer, requireThat } from "../sdk/node/runtime.mjs";
import { createAccessClient } from "../sdk/node/access-client.mjs";
import { createBusiness } from "./business.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
export const manifest = JSON.parse(
  readFileSync(path.join(root, "manifest.json"), "utf8"),
);
export const registration = JSON.parse(
  readFileSync(path.join(root, "registration.json"), "utf8"),
);
export function createService({
  token,
  callbackUrl,
  filename,
  projects,
  fetchImpl,
}) {
  const business = createBusiness({
    filename,
    projects,
    decide: createAccessClient({ callbackUrl, fetchImpl }),
  });
  try {
    const server = createModuleServer({
      manifest,
      registration,
      token,
      handlers: business.handlers,
      workflowHandlers: business.workflowHandlers,
    });
    return {
      server,
      close: async () => {
        if (server.listening)
          await new Promise((resolve) => server.close(resolve));
        await business.close();
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
    filename: path.join(directory, "records.sqlite"),
    projects,
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
