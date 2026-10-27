/** Bilateral registration v1. No business handler is executed by a handshake.
 * Transport authentication belongs to runtime.mjs; this file only compares
 * contracts and checks the same handler maps that runtime dispatches through.
 */
import { createAtomBindings } from "./atoms.mjs";

const plain = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const sorted = (value) =>
  Array.isArray(value)
    ? value.map(sorted)
    : plain(value)
      ? Object.fromEntries(
          Object.keys(value)
            .sort()
            .map((key) => [key, sorted(value[key])]),
        )
      : value;
const equal = (left, right) =>
  JSON.stringify(sorted(left)) === JSON.stringify(sorted(right));
function need(ok) {
  if (!ok)
    throw Object.assign(
      new Error("注册确认未通过，请检查协议、声明与接口绑定。"),
      { code: "registration_contract", status: 422 },
    );
}

/** Fail at service startup on missing/extra handlers or unsupported schemas. */
export function createRegistration({
  manifest,
  registration,
  handlers,
  workflowHandlers = {},
  compile,
  graph = null,
  runtimeProbe = null,
  requiredPlatformFeatures = [],
}) {
  need(Array.isArray(requiredPlatformFeatures) && requiredPlatformFeatures.length <= 20 &&
    requiredPlatformFeatures.every((value) => typeof value === "string" && /^[a-z][a-z0-9.-]{0,79}$/.test(value)) &&
    new Set(requiredPlatformFeatures).size === requiredPlatformFeatures.length);
  const extraFeatures = [...requiredPlatformFeatures];
  need(
    ["1.0", "1.1"].includes(registration?.registration_version) &&
      manifest.protocol_version === "1.2",
  );
  need(
    registration.module_id === manifest.module.id &&
      registration.module_version === manifest.module.version,
  );
  const ids = manifest.capabilities.map((cap) => cap.id).sort();
  if (registration.ai_services !== undefined) {
    need(Array.isArray(registration.ai_services) && registration.ai_services.length <= 16 &&
      (!registration.ai_services.length || registration.permissions.mode === "platform_access"));
    const aiIds = new Set();
    for (const service of registration.ai_services) {
      need(plain(service) && Object.keys(service).sort().join() ===
        "capability_ids,description,id,supported_sources,title" &&
        typeof service.id === "string" && /^[a-z][a-z0-9_.-]{2,119}$/.test(service.id) &&
        service.id.startsWith(manifest.module.id + ".") && !aiIds.has(service.id) &&
        typeof service.title === "string" && service.title.length > 0 && [...service.title].length <= 200 &&
        typeof service.description === "string" && service.description.length > 0 && [...service.description].length <= 2000 &&
        Array.isArray(service.capability_ids) && service.capability_ids.length > 0 && service.capability_ids.length <= 64 &&
        service.capability_ids.every((id) => ids.includes(id)) &&
        new Set(service.capability_ids).size === service.capability_ids.length &&
        Array.isArray(service.supported_sources) && service.supported_sources.length > 0 &&
        service.supported_sources.every((source) => ["platform", "module"].includes(source)) &&
        new Set(service.supported_sources).size === service.supported_sources.length);
      aiIds.add(service.id);
    }
  }
  const modern = registration.registration_version === "1.1";
  if (modern) {
    need(
      equal(graph, registration.module_graph) &&
        typeof runtimeProbe === "function",
    );
    createAtomBindings({ graph, manifest, handlers });
  }
  need(
    equal(Object.keys(handlers).sort(), ids) &&
      Object.values(handlers).every((fn) => typeof fn === "function"),
  );
  need(
    equal(
      Object.keys(workflowHandlers).sort(),
      registration.workflow_operations,
    ) &&
      Object.values(workflowHandlers).every((fn) => typeof fn === "function"),
  );
  for (const capability of manifest.capabilities) {
    compile(capability.input_schema);
    compile(capability.output_schema);
  }
  // Freeze a private copy: later caller mutation cannot change accepted contracts.
  const localManifest = structuredClone(manifest),
    localRegistration = structuredClone(registration);
  return {
    get document() {
      return structuredClone(localRegistration);
    },
    confirm(offer) {
      need(
        plain(offer) &&
          equal(
            Object.keys(offer).sort(),
            [
              "registration_version",
              "nonce",
              "contract_digest",
              "platform_features",
              "manifest",
              "registration",
              ...(modern ? ["runtime_probe"] : []),
            ].sort(),
          ),
      );
      need(
        offer.registration_version === localRegistration.registration_version &&
          /^[0-9a-f]{32}$/.test(offer.nonce) &&
          /^[0-9a-f]{64}$/.test(offer.contract_digest),
      );
      const required = ["invoke-1.1", "shared-hosts-1.0", "registration-1.0"];
      required.push(...extraFeatures);
      if (localRegistration.ai_services?.length) required.push("ai-services-1.0");
      if (modern)
        required.push(
          "registration-1.1",
          "module-graph-1.0",
          "flow-exports-1.0",
          "runtime-observation-1.0",
        );
      if (localRegistration.permissions.mode === "platform_access")
        required.push("access-1.0");
      need(
        Array.isArray(offer.platform_features) &&
          required.every((feature) =>
            offer.platform_features.includes(feature),
          ),
      );
      need(
        equal(offer.manifest, localManifest) &&
          equal(offer.registration, localRegistration),
      );
      const answer = {
        registration_version: localRegistration.registration_version,
        nonce: offer.nonce,
        contract_digest: offer.contract_digest,
        accepted: true,
        checks: {
          platform_contract: true,
          local_manifest: true,
          local_registration: true,
          handlers: true,
          schemas: true,
        },
        capability_ids: [...ids],
        workflow_operations: [...localRegistration.workflow_operations],
      };
      if (!modern) return answer;
      return Promise.resolve(runtimeProbe(offer.runtime_probe)).then(
        (accepted) => {
          need(accepted === true);
          return {
            ...answer,
            checks: {
              ...answer.checks,
              atomic_bindings: true,
              runtime_callback: true,
            },
          };
        },
      );
    },
  };
}
