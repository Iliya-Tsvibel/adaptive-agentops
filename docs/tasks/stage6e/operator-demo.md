# Stage 6.E operator demo and native gate

Use the reviewed dedicated maintenance launch and a trusted, explicitly
configured dispatch UUID. The execution Controller must be stopped.
Provision a separate Controller-owned mode 0700 inspection audit
directory with `provision_inspection_audit` once. Use distinct non-root
inspector, agent, requester, approver, Controller and Target UIDs, with a
dedicated inspector socket directory and access group.

From the configured inspector UID, the Python operator client is
`adaptive_agentops.stage4_boundary.evidence_inspection.inspect_dispatch`.
Pass the protected deployment's inspector endpoint, inspector and
Controller UIDs, and the scoped dispatch UUID. Display only
`result.model_dump_json()`. A denial or failed exchange returns no
evidence. The Controller calls `serve_inspection_once` with the trusted
scope, hardened service configuration, separate source and audit paths.
The Target serves the existing receipt lookup on its protected endpoint.

The disposable [native workflow](../../../.github/workflows/stage6e-native-acceptance.yml)
runs the demo under separate Linux UIDs and exercises both real protected
SQLite stores and Unix sockets. It uses an injected lost acknowledgement
after a real simulated SQLite commit and an abrupt Target process exit
after a real claim commit. The harness removes the verified stale fixture
socket before bringing the same Target state back up. The two paths retain Controller UNKNOWN while
producing different operator results. Native coverage also includes
protected file and socket access, out-of-scope requests, incompatible
mutation frames, lease exclusion, unsafe Target socket metadata, and a
modeled ambiguous inspection audit commit. The harness compares the
original and final execution database bytes.

Ordinary CI and exact-diff review gate publication. The separate native
workflow requires the reviewed merged revision in the `expected_sha` input, the authorized owner
actor, attempt 1 and the campaign budget. Evidence is limited to local
simulated SQLite execution on that disposable runner. Production NO-GO.
