# AI services 1.0

`registration.ai_services` is optional. Old registrations remain valid. A new
service declares `id`, `title`, `description`, concrete `capability_ids`, and
`supported_sources` containing `platform`, `module`, or both. IDs belong to the
module namespace. Services require `permissions.mode=platform_access`; the
handshake must negotiate `ai-services-1.0` when this array is nonempty. No API key,
provider URL, executable handler, or default provider belongs in registration.

## Configuration ownership

Platform administrators use the following same-origin endpoints. Every write
requires the current session and CSRF token. Keys are secret input only and must
never enter invocations, audit details, registrations, exports, or browser storage.

* `GET /api/v1/ai-services`: `{items:[...]}`. Each item contains the declaration,
  `module_id`, `remote_service_id`, `remote_revision`, `source`,
  `platform_profile_id`, `revision`, `remote_enabled`, and `binding_stale`.
* `PUT /api/v1/ai-services/{module_id}/{service_id}/binding`: the binding request
  schema. `expected_revision` is 0 initially. `source` is explicitly `disabled`,
  `platform`, or `module`. Platform source requires a specific existing, enabled
  model profile ID. Other sources require `platform_profile_id:null`.
* `GET` / `PUT /api/v1/ai-services/{module_id}/{service_id}/module-config`: read
  or save the module-owned encrypted configuration, using the configuration
  schemas. Saving this configuration and selecting its source are separate writes.
  The platform proxies the body to a fixed authenticated module control endpoint;
  it does not persist the module's API key. A blank key retains the current key;
  `clear_api_key:true` clears it. Destination changes require a fresh or cleared key.
* `POST /api/v1/ai-services/{module_id}/{service_id}/test`:
  `{expected_revision}` tests only the currently saved source. Returns route
  metadata plus `result` matching the test result schema. This sends a synthetic
  connection test to a real provider; it does not read business data.

The module implements `GET` / `PUT
/platform/v1/ai/services/{service_id}/config` and `POST
/platform/v1/ai/services/{service_id}/test`. These fixed controls use the existing
platform service Bearer authentication. The config GET returns `{revision,profile}`
with the public fields in the response schema. PUT uses `{expected_revision,profile}`;
test uses `{expected_revision}` and returns the test result directly. The module
checks configuration revisions before and after network calls. Service credentials
authorize configuration transport, never a business operation or an arbitrary user.

## Business execution

The module first authorizes its real business object, then sends the current
`X-Platform-Access` ticket with the scoped `request_id`, `operation`, `permission`,
`facts`, and declared `service_id` to `POST /api/v1/ai/resolve`. The platform verifies
current user, grant stamp, capability, module connection, availability, and binding.
The response is `{service_id,source,binding_revision,module_config_revision,
platform_config_digest}`. Module source uses an integer module config revision
and a null platform digest; platform source uses a null module revision and an
opaque 64-character digest of the current encrypted platform profile. This digest
contains no key or supplier connection configuration.

* For `platform`, the module calls `POST /api/v1/ai/chat` with the same scope,
  `binding_revision`, `module_config_revision:null`, the original
  `platform_config_digest`, and `messages`. The central
  gateway calls exactly the bound profile; its key never reaches the module.
  The response adds `result` with normalized `content`, `tool_calls`, `model`,
  optional `finish_reason`, and token `usage`. This version accepts text messages
  only and does not accept tool definitions.
* For `module`, the module checks its local config revision matches the resolved
  revision, calls only its local profile, checks its revision again, and sends the
  same scope plus both revisions and `platform_config_digest:null` to
  `POST /api/v1/ai/validate` before returning or
  committing the result. The platform re-reads the fixed module config metadata.
  No local business mutation should precede this final validation.

`/validate` returns route metadata after checking current permission, availability,
connection, binding revision, and the selected provider configuration revision or
digest. SDKs validate again after `/chat` before committing a business result. The
platform digest covers both the resolve-to-chat window and the chat-to-final-validate
window. Central chat additionally compares the platform profile before and after
the provider request. No branch silently uses a default profile or falls
back to the other source. Disablement, deletion, revocation, restart-invalidated
tickets, configuration changes, timeouts, and malformed responses fail explicitly.

Standalone modules may explicitly use their encrypted module profile with their
own business authentication. Once configured as platform-connected, missing or
expired platform scope is an error, not permission to switch to standalone mode.

## Bounds and persistence

Declarations contain at most 16 services and 64 capability IDs per service. Callback
resolve and validate bodies are at most 16 KiB; chat is at most 64 KiB, 64 text
messages, and 16,000 characters per message. Module configuration requests and
responses are at most 32 KiB. Provider deadlines are explicitly configured from 1
to 300 seconds, without automatic retry or redirects. Access tickets still have
the existing short lifetime (at most 60 seconds); a long response that outlives its
ticket is rejected, so use a suitable provider timeout and start a fresh business
request rather than extending authority silently.

Binding files contain only references and revisions, using atomic file replacement
and a process-local lock. Module profiles and platform profiles encrypt their own
keys. This supports one configuration-writing process per data directory; distributed
locking, delegated background AI jobs, streaming, supplier-specific tools, and
multi-replica credential distribution are outside this version.
