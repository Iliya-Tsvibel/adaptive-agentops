# Stage 3 — Local LLM, Strict Proposals and Evaluation

**Status:** `LOCAL MVP ACCEPTED`. Stage 3's original Incident Investigator PRD remains incomplete. Baseline report: `docs/stage3-acceptance.md`.

## Problem

Stage 2 established deterministic authorization for scripted tool requests. Stage 3 tested whether an **actual local LLM** could interpret bounded health evidence and propose a useful action while having **no independent execution authority**.

## WHY

An LLM is not a policy engine or trusted identity provider. Prompt injection, incorrect observations, malformed JSON and timeouts are normal engineering risks. Giving the model direct native tool execution would create a second path around the Gateway. We chose a **tiny output contract** and reused all accepted Stage 2 controls.

## HOW: one real local preparation path

`trusted RunContext → authorized audited mock health read → schema-validated observation → bounded Ollama inference → strict proposal parser → existing Gateway/PDP → committed pending action → STOP`

- Model: local Ollama `qwen3.5:2b-q4_K_M` at fixed `127.0.0.1:11434/api/chat`.
- Request settings: `think=false`, `stream=false`, `temperature=0`, `num_ctx=2048`, `num_predict=128`.
- Only `NO_ACTION` and `PROPOSE_RESTART` are eligible parsed decisions; no model-supplied role, approval, arbitrary URL or tool permissions.
- Validated observation cap: 1024 bytes serialized UTF-8; bounded request/response sizes 16 KiB/32 KiB and bounded transport/deadline behavior.
- No provider-native tools, conversation-based escalation, automatic retry, downloads or cloud fallback.
- The agent may **prepare** a pending action but cannot approve, reserve or dispatch it. The original Stage 2 operator/Controller path remains separate.
- Regular CI uses mock transports; the accepted live local inference run was separate and explicitly authorized.

### Why parser + PDP after model inference?

Even if a model returns perfectly valid JSON, it remains an **untrusted proposal**. The strict parser enforces shape, but only the trusted Control Plane checks identity, scope and policy. Parsing is not authorization.

### Why local model rather than paid hosted model?

It reduced data egress and additional cost for a narrowly defined experiment. **Local endpoint alone does not prove daemon provenance or no external egress**; these depend on the approved runtime configuration and additional network controls.

## Evaluation methodology

The frozen synthetic dataset had **26 test cases**: **24 scored model decisions** plus **two boundary rejections requiring zero model requests**. The 24 decisions included nine scenarios where proposing restart was expected and fifteen NO_ACTION scenarios. Paired attacks tested prompt injection or forged authority versus clean controls; long and Unicode-rich cases and invalid/oversized observations tested boundaries.

| Measured evidence | Result in the one accepted run |
| --- | --- |
| Cases completed | 26/26 |
| Correct scored model decisions | 24/24 |
| False-positive restart proposals | 0/15 |
| False negatives | 0/9 |
| Injection overrides | 0/4 paired comparisons |
| Boundary rejection | 2/2, no model request |
| Unauthorized side effects | 0 |
| Pending actions | 9 |
| Correlated audit events | 61 |
| Approvals or reservations | 0 |
| Preparation latency | p50 0.671212 s; p95 0.807475 s; max 8.713817 s |

**Important interview caveat:** The 24/24 accuracy is **one run on curated synthetic data**. It does not prove general production model reliability, or that temperature zero guarantees reproducibility. The immutable holdout is **consumed**, so do not treat it as a new blind holdout after tuning.

The p95 uses nearest-rank order statistics (23rd of 24), so the first/cold-start maximum of 8.714 seconds remains in the dataset even though it is beyond p95.

## FAILURE MODE / SECURITY

- Health source unavailable or invalid → do not invent a healthy observation or inferred diagnosis.
- Oversized observation → reject before inference; the boundary rejection creates no model request.
- Malformed model response or unexpected native tool call → reject without bypassing the Gateway.
- Prompt injection in untrusted evidence → should not widen the operations allowed by PDP. This **does not** prove that generated narrative is always accurate.
- SQLite pending-action persistence ambiguous → no newly usable action or retry authority is inferred.
- Model timeout/cancellation → bounded failure; it may leave a local inference computation running but never authorizes target mutation.
- CI and exact archived evaluation manifest are distinct from the operator-reported live model execution; no independent hardware/weights attestation is claimed.

## TRADE-OFF

A small local model and a single decision bit make verification tractable but do not demonstrate broad diagnosis, arbitrary tools, multilingual reports, real GitHub/log provenance, long-term memory or a full incident investigation workflow.

## Interview questions

**What exactly could the model do?** Propose restart or NO_ACTION after a validated read. It could never authorize its own request or call Target directly.

**How do you measure prompt-injection safety?** Test that hostile content does not change enforced privileges or cause unauthorized effects; also test report correctness separately. Four paired synthetic cases are not universal immunity.

**What is data leakage in evaluation?** Reusing the same holdout to improve model prompts makes future scores on it no longer independent.

**How do you interpret zero false positives here?** Zero out of fifteen curated negative examples in one run, not a statistical guarantee of zero false positives in production.

**What was not done in Stage 3?** Complete Incident Investigator with live GitHub read-only integration, `mock.get_logs`, source provenance and final evidence-backed report.

## 60-second pitch

> Stage 3 connected a tightly bounded local Ollama model to the deterministic security pipeline. It could only choose NO_ACTION or propose one restart from validated health evidence; it could not gain tool authority, approve or dispatch. We used a frozen 26-case synthetic evaluation, got 24 of 24 scored decisions correct in one run and verified no unauthorized side effects. We explicitly documented sample-size, provenance and incomplete product-scope limits.

## Evidence

[Stage 3 accepted report](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/docs/stage3-acceptance.md), [evaluation manifest](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/evaluations/stage3_results.json), [adapter](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/adaptive_agentops/ollama_adapter.py), [evaluation tests](https://github.com/Iliya-Tsvibel/adaptive-agentops/blob/main/tests/test_stage3_evaluation.py).
