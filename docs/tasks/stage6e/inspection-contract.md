# Stage 6.E.1–3 inspection slice

Base `6a8062a081e2cd560e67a29764e74d1b2d25ef59`.
[ADR-0002](../../decisions/ADR-0002-stage6-evidence-bridge.md) defines the
approved security design. The authorizing campaign permits combining the
dedicated boundary, acquisition and typed projection as one vertical
slice. Publication requires independent review of the exact candidate,
focused and full tests and ordinary CI. Operator demo and native workflows
have their own subsequent gate.

File scope is the evidence source and inspection modules, tests for that
slice, this contract, the campaign ledger, and additive Target lookup and
connect deadline parameters in the Stage 4 IPC helpers. Default behavior
of existing callers is preserved. No schema or execution-path changes.

The inspection request carries an exact version and a single configured
dispatch UUID. A kernel-authenticated UID is checked before parsing and
authorization precedes dispatch-specific I/O. The execution lease spans
the listener lifetime, acquisition, audit and reply. If the lease directory
is absent or cannot be leased safely, the maintenance listener stays closed. Storage, source and transport
limits apply independently. Inspect authorization confers no grant, approval,
reservation or mutation ability. Only a committed audit permits release.

| Controller observation | Target observation | Reply |
| --- | --- | --- |
| Bound committed receipt | Matching committed receipt | Recorded effect, both |
| Bound committed receipt | Unknown or unavailable | Recorded effect, Controller only |
| Bound committed receipt | Invalid or different effect number | Inconsistent |
| Historical UNKNOWN or missing outcome with bound digest | Matching committed receipt | Recorded effect, Target only, with original Controller status |
| Historical UNKNOWN or missing outcome | Unknown, unavailable or no digest | Unresolved |
| Bound no-send record without a digest | No query | Controller no-send |
| Invalid anchor, inconsistent history or malformed correlated reply | Invalid or no query | Inconsistent, without positive attribution |
| Missing or unavailable Controller evidence | No query | Unresolved |

The two sources are read independently. The response uses categorical
source and historical outcome fields and does not expose audit keys,
clock values, raw records, effect numbers or exception text.
