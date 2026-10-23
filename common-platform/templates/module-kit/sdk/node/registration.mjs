/** Bilateral registration v1. No business handler is executed by a handshake.
 * Transport authentication belongs to runtime.mjs; this file only compares
 * contracts and checks the same handler maps that runtime dispatches through.
 */
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
}) {
  need(
    registration?.registration_version === "1.0" &&
      manifest.protocol_version === "1.2",
  );
  need(
    registration.module_id === manifest.module.id &&
      registration.module_version === manifest.module.version,
  );
  const ids = manifest.capabilities.map((cap) => cap.id).sort();
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
            ].sort(),
          ),
      );
      need(
        offer.registration_version === "1.0" &&
          /^[0-9a-f]{32}$/.test(offer.nonce) &&
          /^[0-9a-f]{64}$/.test(offer.contract_digest),
      );
      const required = ["invoke-1.1", "shared-hosts-1.0", "registration-1.0"];
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
      return {
        registration_version: "1.0",
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
    },
  };
}
