# Registered source object reads 1.0

`POST /api/v1/access/source-reads` is an optional server callback advertised as
`source-reads-1.0` in the registration offer. It uses the existing current target
`X-Platform-Access` ticket. Browser cookies cannot replace it. It does not issue
source credentials to consumers or authorize arbitrary capability calls.

The request follows `source-read-request.schema.json`: `source_read_version`,
the target request ID/operation/permission, target repository facts, a source
module/object reference/fixed revision, and optional source-specific `read_input`.
No caller actor, URL, credential or object-reference override is accepted.

The platform rechecks target authorization, resolves an admitted published source
module and its registered `data_objects.operations.read` **query**, and calls
the existing `objects.read` transport with the same real actor and a freshly
bound source ticket. The source checks current permission and repository facts,
including field/record projection. The platform then rechecks the target and
fixed source reference/revision before returning:

```json
{
  "source_read_version":"1.0",
  "source":{"module_id":"example.source","object_ref":{"object_type":"example.source.snapshot","object_id":"S1"},"revision":1},
  "projection":{"object_ref":{"object_type":"example.source.snapshot","object_id":"S1"},"revision":1,"state":"published","data":{},"allowed_actions":[]}
}
```

The 16 KiB request and existing 1 MiB projection budgets apply. The SDK callback
has a total timeout and bounded response reader. Signed internal ancestry rejects
cycles and limits a read chain to four consumers. Tickets retain the existing
45-second default lifetime and are never persisted as source proof.

Business category, release state, purpose and domain mapping remain explicit
consumer/source rules. A successful read does not authorize raw data download or
future reads. A source change/revocation/offline error fails closed before the
consumer commits. Each module must re-read on each action requiring that source,
including later exports, and must retain source identity/version in its own
domain object. Old fixed versions require actual immutable source objects; a
mutable latest record cannot silently stand in for a requested old version.

Source readers use the same query permission in registration and their
`objects.read` handler. If an export requires a separate permission, register a
separate export/snapshot object reader rather than adding protected export data
to an ordinary read projection.

Server-only clients: `sdk/node/source-read-client.mjs` and
`sdk/python/source_reads.py` in the portable module kit. Their callback address
comes from deployment configuration, never ordinary invocation input. Failure
raises a stable error code without logging tickets or returning remote prose.

Consumers that depend on this seam pass
`requiredPlatformFeatures: ["source-reads-1.0"]` to `createModuleServer`, or
`required_platform_features=["source-reads-1.0"]` to Python `Registration`.
Registration rejects an older platform before publication. This is local SDK
configuration; the registration 1.1 wire shape and other modules' requirements
remain unchanged.
