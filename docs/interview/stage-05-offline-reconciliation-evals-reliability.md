# Stage 5 — Synthetic Offline Reconciliation, Diagnostics, Evals & Reliability

**Adaptive AgentOps / Secure AI Agent Control Plane — Interview Lab**  
**Status:** **OWNER ACCEPTED — LOCAL SYNTHETIC OFFLINE / NONPRODUCTION SCOPE** (26 Nov 2026).  
**Post-acceptance hardening:** completed 27 Nov 2026; issue #35 closed.  
**Production:** **NO-GO**.

> This is a learning and interview document, not a new architecture contract or an authorization to operate live services. It follows the Stage 0–4 Interview Lab convention: **PROBLEM → WHY → HOW → FAILURE MODE → SECURITY → TRADE-OFF → EVIDENCE → MY ROLE**. Facts about tested scope are separated from design proposals and production aspirations.

## Executive summary

Stage 4 implemented **one protected, local, OS-identity-bound simulated dispatch path** with Controller approval, Target replay claims and the possibility of an unresolved `UNKNOWN` result after a lost acknowledgement. Stage 5 addressed a different safety question: **How can we interpret durable evidence about an ambiguous dispatch without turning an investigation into another execution attempt?**

We deliberately started with a **pure, synthetic, offline evidence projector**, not a live operator feature. It validates bounded Controller/Target observations, correlates dispatch/action/approval/session and digest records, preserves capture-order and source-coverage uncertainty, and produces a classified result. A separate, opt-in formatter emits small, redacted JSON. We froze an independent evaluation oracle before scoring, then exercised interruption and modeled-fault paths. The code **cannot issue grants, approve actions, read protected live stores, call a Target or retry a mutation**.

**Final evidence:** 33 pre-existing frozen regression fixtures; **16/16** new independently frozen synthetic evaluation cases (13/13 classification, 13/13 provenance; three formatter-only cases), **zero critical evaluation failures**, **6/6** synthetic reliability scenarios; **1,846 passing ordinary CI tests** at formal Stage 5 acceptance. A later hardening PR added **19** further contract regressions, for **1,865 passing CI tests**, without changing Stage 4 security code or the Stage 5 runtime module. Stage 5 used **0/4** native dispatches; the budget is closed, with no carry-forward.

## 1. PROBLEM: why this stage existed

Stage 4 intentionally prioritizes safety over liveness. A Controller may consume approval and reserve a single dispatch; the Target may commit a simulated effect; then a reply may be lost. The Controller can retain `UNKNOWN` even if an effect was recorded. Alternatively, a Target may have a replay claim but no committed effect. A timeout or missing record is **not** proof that nothing happened.

A dangerous design would classify `UNKNOWN` as `FAILED` and automatically send the mutation again. That turns a diagnostic gap into a potential duplicate side effect. The Stage 5 objective was a **bounded, evidence-backed answer** for one simulated dispatch: what does each source actually establish, what conflicts, and what remains unknown?

The initial live-first Draft PR #26 proposed authenticated operator `RECONCILE_DISPATCH`. The later owner-backed Stage 5.0 plan **changed the first slice to offline synthetic-only** because new live read authority, disclosure rules, and protected-state access deserved a distinct security gate. PR #26 was closed as superseded; Stage 5.0 became PR #27.

## 2. WHY: the architecture choice and its trade-offs

**Chosen:** pure offline Python module + versioned synthetic inputs + deterministic projector + opt-in redacted formatter + frozen scored cases + modeled reliability checks.

**Not chosen:** direct SQLite inspection, authenticated live operator IPC, automatic repair/retry, operator CLI, runtime telemetry service, cloud stack or new identity framework. These remain outside Stage 5 acceptance.

Why this order?

1. **Contain authority.** A pure transformation of caller-provided synthetic data cannot accidentally gain Controller/Target privileges through a new IPC or DB path.
2. **Separate safety from data acquisition.** First define classification semantics; later define how a trustworthy, scoped custodian would capture evidence and prove provenance.
3. **Make false certainty testable.** Freeze examples of inconsistent, partial, missing and stale observations before scoring.
4. **Avoid scope creep.** No need for Kafka, Kubernetes, OpenTelemetry stack or agent framework to demonstrate safe evidence interpretation.

**Trade-off:** this is not yet a useful live incident operator. It is a tested analytical component ready to inform the design of one, not evidence that it already handles real incidents.

## 3. HOW: system boundaries and data flow

```text
            SYNTHETIC, VERSIONED EVIDENCE FIXTURE
       (one dispatch, modeled Controller + Target sources)
                             |
                             v
       decode_fixture(data: bytes) — strict typed validation
        max 64 KiB, one observation/source, <=16 rows/source
                             |
                             v
                  reconcile(Fixture) — PURE
        validate source coverage and immutable bindings
        compare capture order / matched IDs / digests
        distinguish conflicts, positives and uncertainty
                             |
                             v
                    typed Result + reason
                    /                 \
                   v                   v
        internal format_result()    format_local_diagnostic()
        NOT approved for public     opt-in, <=256 ASCII bytes,
        operator disclosure         fixed JSON allowlist, no raw IDs

          No Controller/Target imports, DB, IPC, network,
          dispatch, approval, retry, file writes, or mutation.
```

**Primary implementation:** [`adaptive_agentops/offline_reconciliation.py`](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/1002b9c838fb42041b45f6b73104bc87cc20490f/adaptive_agentops/offline_reconciliation.py). The post-acceptance hardening did not modify this application file.

### Input and schema contract

- `decode_fixture(bytes)`: strict UTF-8 JSON, top-level version `1`, duplicate-key rejection including nested objects, unexpected-field rejection and typed Pydantic models; rejects inputs over **64 KiB**.
- One `dispatch_id`, Controller and Target source; each source has `coverage`, synthetic `capture` label and observation `order`; at most **16 records per source**.
- Coverage vocabulary: `COMPLETE`, `PARTIAL`, `UNAVAILABLE`, `INVALID`. A complete empty source is meaningfully different from a partial empty source.
- Correlation: Controller reservation and action ID; session/run binding; matching approved-and-consumed approval; matching event/status; request digest; Target claim/receipt and effect number.
- Input fields and capture identifiers are **synthetic assertions**, not independently authenticated real-store provenance.
- Immutability and fail-safe output are asserted in tests. No input field or model-generated claim supplies authority.

### Classification — do not oversimplify

| Classification | Safe interpretation | What not to infer |
| --- | --- | --- |
| `INVALID` | Input/source malformed or explicitly invalid | “The operation did not happen” |
| `INCONSISTENT` | Contradictory or unbound evidence | Choose the convenient positive record |
| `SIMULATED_EFFECT_RECORDED` | Corroborated synthetic evidence of a **recorded simulated SQLite effect** | Real-world exactly-once or permission to repeat |
| `CONTROLLER_NO_SEND_RECORDED` | Corroborated durable Controller pre-send stop for that attempt | Target never ran in every possible external system |
| `UNRESOLVED` | Some observations exist but don't establish finality | Safe automatic retry |
| `UNAVAILABLE` | Required source availability is missing under the modeled coverage | Absence of effect |
| `MISSING_EVIDENCE` | Relevant complete observations contain no rows | Guaranteed non-execution |

**Precedence matters:** malformed/invalid evidence and material conflicts block positive attribution; a positive requires source/binding corroboration; `UNKNOWN` remains unresolved when no higher-confidence matching evidence exists. The exact `UNRESOLVED`/`UNAVAILABLE` result also depends on whether observations are present; do not describe the classifier as a flat success/failure enum.

### Worked example: lost acknowledgement

1. Stage 4 Controller reserves a unique `dispatch_id`, consumes approval and records an outbound attempt under protected state.
2. Target durably claims that dispatch/action/digest and may atomically write simulated counter effect + receipt.
3. The reply disappears; Controller persists/returns `UNKNOWN`.
4. A **synthetic** Stage 5 fixture provides the Controller anchor, approved/consumed binding, known request digest and a matching Target committed effect number.
5. The projector returns `SIMULATED_EFFECT_RECORDED` with `TARGET` attribution, while **preserving** the Controller's earlier `UNKNOWN` fact.
6. The diagnostic JSON exposes the allowlisted status/coverage/effect-source labels, **not** IDs, digest, payload, exception text or effect number. No dispatch is attempted.

The Target's Stage 4 lookup returns `UNKNOWN` both when there is **no matching claim** and when a retained claim has **no effect receipt**. A synthetic projection may distinguish these only when an explicitly complete, independently trustworthy source description exists. Neither `UNKNOWN` case authorizes retry.

### Capture-time boundary

`controller.order` and `target.order` describe a synthetic observation ordering. At equal or later Target order, a complete missing or null receipt may contradict an already recorded Controller effect; an earlier Target observation may simply predate the effect. **These integer labels are not proof of synchronized clocks or a consistent distributed snapshot.** Future real capture needs independently verified per-store provenance, scope and ordering assumptions.

## 4. Stage 5 work packages — what we actually delivered

| Stage | Implementation / decision | Validation and evidence |
| --- | --- | --- |
| **5.0 Architecture** | Reconciled a newer offline-first threat model with existing Stage 4 state; superseded live-first draft PR #26; no new live authority. | [PR #27](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/27), merged `fd15fde5569df7a25e2bf848ff3b95605b681199`; CI **1,755**. |
| **5.1 Offline projection** | Typed bounded `Fixture`, `Result`, `decode_fixture()`, `reconcile()`, `analyze_fixture()`; 33 frozen v5 regression cases, coverage, binding, digest, receipt, capture-order semantics. | [PR #28](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/28), merged `3ed9aed1b3cba9237eb67a9dc2e4ceb697412c26`; CI **1,796**. |
| **5.2 Diagnostics** | `format_local_diagnostic(Result)` emits one opt-in, allowlisted ASCII JSON object <=256 bytes; no I/O or authority; 84 focused PASS. | [PR #29](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/29), merged `998f5b9659161855d45a0579326bba36bf8c5eb5`; CI **1,839**. Local measured incremental overhead **12.782 µs/fixture** (33 fixtures; setup-specific, not an SLO). |
| **5.3 Evaluations** | 16 **new** independently frozen oracle cases, four families (corroboration, uncertainty, invalid/encoding, disclosure); original 33 regression cases excluded from the new denominator. | [PR #30](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/30), merged `ffdd6ddeede2cbc522936f1316fa8b23806daaa2`; **16/16**, **13/13** classification and provenance, 0 critical, CI **1,840**. |
| **5.4 Reliability** | Six bounded scenarios: parser recursion; parse interruption; formatter interruption; Target unavailable/partial/complete; contradictory evidence; size/row limits. **Only** in-process injection and modeled conditions. | [PR #31](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/31), implementation SHA `1002b9c838fb42041b45f6b73104bc87cc20490f`; **6/6**, CI **1,846**. |
| **5.5 Acceptance** | Published independently assessed acceptance report, preserved implementation SHA distinct from documentation commits and recorded formal owner decision. | [PR #32](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/32) report at `0d87fffd9067b88e19c3227f72aadca87e7d3d5a`; [formal owner acceptance](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/32#issuecomment-7203198226); docs closure [#33](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/33) and sign-off link [#34](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/34). |
| **Post-acceptance hardening** | 19 new contract regressions, direct Pydantic dependency, clarified internal formatter disclosure and reviewer evidence process, protected `main`. **No Stage 4 security / Stage 5 runtime changes**. | [PR #36](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/36), merged `0ab3c0d1e09eb4c53269ac8d8245fb2d9d62720b`; [CI](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45970721953) **1,865**; [issue #35](https://github.com/Iliya-Tsvibel/adaptive-agentops/issues/35) closed as completed. |

### Frozen data and reproducibility

- Original Stage 5.1 v5 regression corpus: **33 fixtures**, SHA-256 `3674b2b94b828084d6a47bc7136e1d8272bd9caef058cdebb2fc168b48c193ab`. The original corpus stayed byte-identical after Stage 5.3 and hardening.
- New Stage 5.3 v1 scored corpus: **16 cases**, SHA-256 `7c7341a596ca034ea8f60cfbd932f7108e8c37b8b2af07579211d9cc44d0c755`. A separate read-only reviewer checked the expected labels **before scoring**, including one pre-score correction distinguishing earlier complete from partial evidence. After freeze, labels weren't revised to raise scores.
- Four families: `corroboration` 4/4; `uncertainty` 4/4; `invalid` 4/4; `diagnostic` 4/4. **Thirteen** cases apply classification/provenance assertions; **three** formatter-only cases are excluded from those denominators. Every case was reconstructed twice; repeats did not inflate denominator.
- Ordinary full-suite count is **not the number of independent Stage 5 evaluation cases**: the scored 16-case evaluation runs inside a pytest test item; the six reliability scenarios contribute six focused test items. Report all denominators separately.
- New post-acceptance contract regression cases are additional ordinary tests, **not a newly blinded 19-case holdout**.

## 5. FAILURE MODES: how the system behaves

| Failure / adversarial observation | Expected safe interpretation or behavior | Limits of proof |
| --- | --- | --- |
| Controller says `UNKNOWN`, Target has matching committed receipt | Attribute recorded simulated effect to corroborated Target evidence; retain original Controller status. | Requires trustworthy provenance; tested synthetic only. |
| Controller reservation exists, Target source unavailable | Do not infer failure or retry eligibility. | No live Target checked. |
| Missing Controller anchor or approval, mismatched action/session/run/digest | No positive classification. `UNRESOLVED` for partial evidence; `INCONSISTENT` for contradictory complete evidence, per exact contract. | Proven over curated/contract cases, not all possible malformed states. |
| Target receipt absent or null | Coverage and observation order determine whether this is missing, unresolved or conflicting; never implicit non-execution. | Stage 4 lookup deliberately collapses missing claim/null receipt into `UNKNOWN`. |
| Controller durable pre-send stop plus matching contradictory Target effect | `INCONSISTENT` (do not pick one side). | Synthetic model. |
| Invalid/oversized JSON, duplicate keys, wrong types/extra fields | Reject as `INVALID` with bounded generic reason; no positive attribution. | Python JSON/Pydantic boundary, not real storage corruption detection. |
| Forged `Result` custom hooks / oversized tuple / sensitive canaries | Return constant `UNRECOGNIZED` diagnostic or reject wrong input type; don't evaluate unchecked hooks. | `format_result()` is internal and not an approved public redaction boundary. |
| Parser recursion (`RecursionError`) | One modeled parser call; public analyze returns `INVALID_FIXTURE`, no effect attribution. | In-process exception injection. |
| Parser/serializer `KeyboardInterrupt` | Propagate interruption, no generated result/diagnostic for that invocation, no automatic retry. | No OS crash, process kill or SQLite recovery was tested. |
| Source coverage unavailable/partial/complete or conflicting equal-order evidence | Preserve distinct labels, absence and uncertainty; suppress unsafe positive attribution on conflict. | Capture/order is synthetic, not independent real-store provenance. |

## 6. SECURITY: what we proved, and what we did not

**Within accepted Stage 5 scope:**

- No new Controller, Target, approval, OS identity, IPC, schema or dispatch authority. The Stage 4 security-boundary application modules were unchanged by Stage 5 and its hardening.
- Pure synthetic data processing with no filesystem/network/SQLite/logger calls by units under test; bounded input and allowlisted diagnostic output.
- Explicit conservative interpretation of missing/inconsistent evidence; **no retry authority** from a classifier status, no automatic reconciliation repair.
- Deterministic test evidence: pinned datasets, negative/critical assertions, independent oracle freeze, exact CI and merged-tree correspondence.
- CI test phase uses offline Python execution in a network-disabled container; relevant image/dependency artifacts pinned as specified by the GitHub workflow. This is ordinary software evidence, **not** native UID/UDS acceptance.

**Not proved or not implemented:** trusted real Controller/Target snapshot capture, per-store provenance, authenticated historical operator read, audit of live disclosures, actual SQLite backup/recovery, crash atomicity on real stores, tamper-proof audit, human MFA, distributed finality, production reliability or external exactly-once effect.

**Distinct scopes:** Stage 4 native Linux **32/32 markers** applied to Stage 4 executable SHA `34c38d9087b5c34b97fa3d4b68cafc3dc8219e0a`. Stage 5 native usage is **0/4** and its simulated reconciliation is not native security acceptance. Unused campaign dispatch authority does not roll forward.

## 7. Engineering autonomy, independent review and GitHub gates

The owner used Codex as a **Builder / engineering orchestrator**, with separate read-only reviewer agent contexts, bounded work-package contracts, exact-diff fingerprints, CI and verified squash-merges. The owner retained acceptance and any new privileged/security architecture approval.

**Important limitation:** local agent reviewer reports are **not** submitted GitHub reviews or a genuine second person. Historical reviewer contexts were not OS-isolated read-only security principals. We shouldn't imply external certification or GitHub platform approval.

Post-acceptance PR #36 improved the developer-loop evidence protocol: preserve reviewer context, read-only conditions and limits, base/head SHA, tree, exact diff SHA-256, findings, repair history, CI, and merge correspondence; no reconstruction of missing past reports.

The now-active [main ruleset](https://github.com/Iliya-Tsvibel/adaptive-agentops/rules/28222411) requires PR + GitHub Actions `test`, blocks force pushes and branch deletion and has no bypass actors. **Required GitHub approving reviews = 0**, intentionally, because this is a solo-maintained repository. A self-approval is not an independent code review.

### Why exact trees mattered

GitHub PR CI runs on a **candidate head**; squash merge changes commit IDs. Verifying that the *candidate Git tree* equals the *merged Git tree* gives evidence that the merged file contents were the ones tested/reviewed. This is stronger than simply reading “CI green.” It does not itself prove reviewer independence or production readiness.

## 8. TRADE-OFFS and criticism you should volunteer in an interview

1. **High-confidence labels vs throughput:** conservative `UNKNOWN`/conflict can leave a legitimate operation unresolved. We prefer safety to speculative retry.
2. **Synthetic fixtures vs real operational value:** Stage 5 validates reasoning before trusting real capture; it does not yet investigate live dispatches.
3. **One host vs distributed:** Stage 4's mutual UDS/UID security cannot simply be copied to Kubernetes, where containers, namespaces, volumes and workload identities change the trust model.
4. **Tiny deterministic evaluation vs universal coverage:** 16 novel cases, 33 preserved regressions and 19 later contract tests are useful, but not statistics of real-world incident frequency or proof of no false certainty on arbitrary inputs.
5. **Lightweight diagnostics vs telemetry:** opt-in pure formatter is not operational observability infrastructure, durable audit or incident-dashboard functionality.
6. **Solo repo independence:** agent reviewer reports improve scrutiny but lack separate human/platform approval and OS-enforced reviewer isolation. GitHub now enforces PR/CI, not a second reviewer.
7. **Output API:** `format_result()` interpolates internal reason text without a disclosure allowlist. Never expose it as an operator/public response; the bounded `format_local_diagnostic()` is the approved output.
8. **No extra spend:** deterministic offline CI and existing Python stack avoided cloud, LLM and hosted telemetry cost; this reduced realism for live-operations evidence.

## 9. Questions an interviewer might ask — defensible answers

**Q1. Why not simply retry an UNKNOWN dispatch?**  
Because an acknowledgement can be lost *after* the Target commits. `UNKNOWN` describes our knowledge, not the effect. Retrying could duplicate mutation. Stage 4 records durable replay evidence; Stage 5 classifies it read-only but never turns an observation into a new permit.

**Q2. What does `SIMULATED_EFFECT_RECORDED` actually mean?**  
Within the fixture contract, Controller bindings and a matching effect record support a *recorded simulated SQLite effect*. It is not proof of execution in Kubernetes or another external system, and only means as much as the input provenance can support.

**Q3. How do you avoid trusting arbitrary Target JSON?**  
First strict typed input validation and exact immutable correlation; for a **future live interface** there must also be a trusted capture/attestation boundary. Stage 5 alone is intentionally not a live provenance solution.

**Q4. Why distinguish `PARTIAL` from `COMPLETE` observations?**  
An empty partial observation says nothing about omitted rows; a complete, correctly scoped observation can support a missing-row statement. Mixing them creates false certainty, especially across mismatched capture times.

**Q5. Why does capture order matter?**  
An earlier Target snapshot cannot invalidate a later Controller effect by absence. A same-order or later, complete conflicting observation may signal inconsistency. Synthetic order values are not real cross-store clock synchronization.

**Q6. Why separate `reconcile()` from diagnostics?**  
Classification operates on typed internal evidence. Diagnostics rebuild a tiny allowlisted output; no raw source rows or identifying fields cross the output boundary. Diagnostic logging doesn't grant authority.

**Q7. What happened if the JSON parser raised `RecursionError`?**  
The public analyzer normalized this into an invalid synthetic fixture result without positive attribution. A `KeyboardInterrupt`, in contrast, propagated; we did not fabricate a completed analysis or retry. Both were **in-process tests**, not real crash recovery.

**Q8. Why freeze labels before running the evaluator?**  
Otherwise we could adjust expected outputs to fit the implementation and report inflated accuracy. We independently checked the 16-case oracle hash before scoring, retained the old 33-case corpus separately, and considered a single critical false-certainty event a failure regardless of aggregate score.

**Q9. Is 1,865 passing tests evidence of 1,865 unique security scenarios?**  
No. That's the full suite after PR #36. Stage 5's new scored corpus has 16 cases; six reliability scenarios are separate. The 19 additional cases are contract regressions. We always preserve denominators and the type of evidence.

**Q10. What did 0/4 native dispatches prove?**  
Nothing about Stage 5 native identity boundaries—none ran. They show the approved campaign avoided privileged/native tests it did not need. Stage 4's separate 32/32 native security markers are historical, bound to its exact executable SHA.

**Q11. Was the code independently reviewed?**  
Separate Codex read-only reviewer contexts examined exact diffs and documented PASS, and CI candidate/merged trees were compared. We do not claim separate human GitHub approvals or OS-enforced sandbox isolation. The revised reviewer evidence protocol makes that limitation explicit.

**Q12. What would you do next for a real operator?**  
Define a new owner-approved security contract for trusted read-only Controller/Target evidence intake: capture identity, source consistency, scope and digest-verification, lifecycle/retention, authorization and disclosure audit. Then run negative tests showing no mutation, no privileged data leaks and preserved uncertainty. Do **not** simply point the synthetic parser at live DB files.

**Q13. Did Stage 5 modify Stage 4 identity or dispatch controls?**  
No. Stage 5 added a separate offline Python analysis module, synthetic manifests and tests; the later hardening added tests/dependency metadata/docs. Accepted Stage 4 security modules were unchanged.

**Q14. What are the real limitations of the CI test environment?**  
It provides repeatable, network-disabled Python tests with pinned inputs and dependencies. It does not prove the security properties of different Linux UIDs/UDS, actual host process interrupts, SQLite backup/recovery or production topology.

**Q15. What would you do differently?**  
Get durable original reviewer reports and real GitHub rules earlier; we retrofitted branch rules and review provenance in PR #36. For real evidence, design source provenance before accepting statuses as operator-grade facts. Keep the synthetic proof narrowly named to avoid overstating readiness.

## 10. 60-second English interview pitch

> After building a local OS-identity-bound agent control plane, I faced an ambiguous execution problem: the Controller could report UNKNOWN even when a simulated Target effect had committed. Blind retries would have been unsafe. For Stage 5, I deliberately built a pure, synthetic offline reconciliation component rather than introducing new live operator privileges. It validates tightly bounded Controller and Target evidence, correlates action, approval, dispatch, digest and effect records, distinguishes incomplete or conflicting observations, and produces redacted diagnostics without any execution authority. We froze an independently reviewed 16-case evaluation oracle before scoring, passed all cases with zero critical failures, and tested six in-process and modeled reliability scenarios. The original 33-case regression corpus was preserved; follow-up hardening brought the repository suite to 1,865 passing tests and enforced PR/CI branch protection. I also documented the exact limitation: this proves synthetic evidence handling, not trusted live capture, real storage recovery or production readiness.

## 11. 3-minute whiteboard narrative

**First minute — problem and boundary:** Draw Controller → Target, the Stage 4 atomic approval consumption/reservation and Target replay claim; circle the lost acknowledgement → Controller `UNKNOWN`. Say: no automatic retry; missing knowledge is not proof of no effect.

**Second minute — Stage 5 design:** Draw synthetic Controller/Target observations → strict decoder → typed correlation → precedence-driven classifier → redacted formatter. Label fixed input caps, complete/partial/unavailable, capture order and binding checks. Explain positive effect only with corroborated data and why Stage 5 cannot read live stores.

**Third minute — verification/limitations:** Show 33 frozen regression cases, 16 independently frozen scored cases, 6 modeled/in-process reliability scenarios, 19 post-acceptance contract regressions, exact candidate/merge tree, 1,865 full CI passes, branch protection. Conclude with the missing trusted evidence capture and operator authorization as the next security gate.

## 12. MY ROLE — honest ownership narrative

The project owner drove scope, threat-model assumptions, architecture trade-offs, campaign authorization, independent verification requirements and final acceptance decisions. **Codex did much of the implementation/testing/PR orchestration.** The defensible claim is *architecture ownership, security governance, acceptance evidence and ability to explain/debug the implementation*—not “I personally hand-coded every Python line” or “a second human security team certified production.”

A useful STAR summary:

- **Situation:** uncertainty after one-use dispatch and Target reply loss.
- **Task:** provide bounded evidence interpretation without introducing execution power.
- **Action:** choose synthetic offline-first architecture, freeze oracle, insist on negative uncertainty tests, separate read-only reviewer, exact CI/tree matches and an explicit owner sign-off; then improve governance after audit.
- **Result:** accepted, isolated analytical slice, no new runtime authority, 16/16 novel evaluations, 6/6 reliability scenarios, 1,865 repo tests after hardening and enforced PR+CI; live operational integration remains a future gated task.

## 13. Sources and accepted exact revisions

- [Canonical MasterBrief](https://github.com/Iliya-Tsvibel/adaptive-agentops) is a **Project Source**, not a GitHub repository file; check it for current status and career roadmap.
- [Stage 5.0 plan](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/tasks/stage5/5.0-architecture-security-plan.md), [5.1 contract](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/tasks/stage5/5.1-offline-fixture-contract.md), [5.2 contract](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/tasks/stage5/5.2-bounded-local-diagnostics-contract.md), [5.3 contract](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/tasks/stage5/5.3-deterministic-evaluation-contract.md), [5.4 contract](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/tasks/stage5/5.4-synthetic-reliability-contract.md).
- [Stage 5 campaign ledger](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/campaigns/stage5-progress.md); [acceptance report](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/stage5-acceptance.md); [formal owner sign-off](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/32#issuecomment-7203198226).
- [Stage 5 code](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/adaptive_agentops/offline_reconciliation.py), [regression tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_offline_reconciliation.py), [frozen eval runner](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_stage53_deterministic_evaluation.py), [reliability tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_stage54_synthetic_reliability.py), [extra contract regressions](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_stage5_hardening.py).
- [PR #36 hardening](https://github.com/Iliya-Tsvibel/adaptive-agentops/pull/36); [CI 45970721953](https://github.com/Iliya-Tsvibel/adaptive-agentops/actions/runs/45970721953); [Protect main ruleset](https://github.com/Iliya-Tsvibel/adaptive-agentops/rules/28222411); [Issue #35 closed](https://github.com/Iliya-Tsvibel/adaptive-agentops/issues/35).
- **Accepted Stage 5 implementation:** `1002b9c838fb42041b45f6b73104bc87cc20490f`; **original assessment publication:** `0d87fffd9067b88e19c3227f72aadca87e7d3d5a`; **post-hardening verified main:** `0ab3c0d1e09eb4c53269ac8d8245fb2d9d62720b` (27 Nov 2026).

**Rehearsal rule:** open the code and one negative test before each interview; never assert a stronger guarantee than its exact accepted test and trust boundary prove.