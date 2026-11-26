"""Pure reconciliation of bounded synthetic Stage 5.1 evidence."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4


MAX_BYTES = 64 * 1024
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Effect = Annotated[int, Field(ge=1, le=10_000)]
Outcome = Literal["UNKNOWN", "RESERVED_NO_SEND", "SIMULATED_EFFECT_COMMITTED"]
Coverage = Literal["COMPLETE", "PARTIAL", "UNAVAILABLE", "INVALID"]
Status = Literal[
    "INVALID", "INCONSISTENT", "SIMULATED_EFFECT_RECORDED",
    "CONTROLLER_NO_SEND_RECORDED", "UNRESOLVED", "UNAVAILABLE", "MISSING_EVIDENCE",
]


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class _Key(_Record):
    dispatch_id: UUID4
    action_id: UUID4


class ActionBinding(_Record):
    action_id: UUID4
    session_id: UUID4
    run_id: UUID4


class ApprovalBinding(ActionBinding):
    approval_id: UUID4
    consumed: bool
    decision: Literal["APPROVED", "REJECTED"]


class DispatchEvent(_Key):
    session_id: UUID4
    status: Outcome


class ControllerRow(_Key):
    digest: Digest | None
    session_id: UUID4
    run_id: UUID4
    approval_id: UUID4
    action: ActionBinding | None
    approval: ApprovalBinding | None
    outcome: Outcome | None
    event: DispatchEvent | None
    effect_number: Effect | None


class TargetRow(_Key):
    digest: Digest
    effect_number: Effect | None


class _Source(_Record):
    coverage: Coverage
    capture: Annotated[str, Field(pattern=r"^fixture-[0-9]{1,6}$")]
    order: Annotated[int, Field(ge=0, le=1_000_000)]


class ControllerSource(_Source):
    rows: Annotated[tuple[ControllerRow, ...], Field(max_length=16)]


class TargetSource(_Source):
    rows: Annotated[tuple[TargetRow, ...], Field(max_length=16)]


class Fixture(_Record):
    version: Literal[1]
    dispatch_id: UUID4
    controller: ControllerSource
    target: TargetSource


class Result(_Record):
    status: Status
    reason: str
    controller_coverage: Coverage | None = None
    target_coverage: Coverage | None = None
    controller_capture: str | None = None
    target_capture: str | None = None
    controller_outcome: Outcome | None = None
    target_fact: Literal["CLAIM_WITH_EFFECT", "CLAIM_WITHOUT_EFFECT", "NO_ROW", "NOT_OBSERVED"] | None = None
    effect_number: Effect | None = None
    effect_sources: tuple[Literal["CONTROLLER", "TARGET"], ...] = ()
    capture_order: Literal["CONTROLLER_FIRST", "TARGET_FIRST", "SAME_ORDER"] | None = None


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate field")
        obj[key] = value
    return obj


def decode_fixture(data: bytes) -> Fixture:
    """Validate a bounded, versioned synthetic fixture without external I/O."""
    if type(data) is not bytes or len(data) > MAX_BYTES:
        raise ValueError("Fixture must be bounded bytes")
    try:
        text = data.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_unique_object)
        if type(parsed) is not dict or type(parsed.get("version")) is not int:
            raise ValueError
        return Fixture.model_validate_json(text)
    except (ValueError, RecursionError):
        raise ValueError("Invalid synthetic fixture") from None


def reconcile(fixture: Fixture) -> Result:
    """Classify synthetic evidence. Results carry no operation authority."""
    controller, target = fixture.controller, fixture.target
    coverage = (controller.coverage, target.coverage)
    facts = dict(controller_coverage=coverage[0], target_coverage=coverage[1],
                 controller_capture=controller.capture, target_capture=target.capture)
    if any(source.coverage == "INVALID" or (source.coverage == "UNAVAILABLE" and source.rows)
           for source in (controller, target)):
        return Result(status="INVALID", reason="INVALID_SOURCE", **facts)

    if controller.order < target.order:
        facts["capture_order"] = "CONTROLLER_FIRST"
    elif controller.order > target.order:
        facts["capture_order"] = "TARGET_FIRST"
    else:
        facts["capture_order"] = "SAME_ORDER"

    if len(controller.rows) > 1 or len(target.rows) > 1:
        return Result(status="INCONSISTENT", reason="DUPLICATE_ROWS", **facts)
    c = next(iter(controller.rows), None)
    t = next(iter(target.rows), None)
    if any(row.dispatch_id != fixture.dispatch_id for row in (c, t) if row):
        return Result(status="INCONSISTENT", reason="DISPATCH_MISMATCH", **facts)

    facts["controller_outcome"] = c.outcome if c else None
    facts["target_fact"] = "NO_ROW" if target.coverage == "COMPLETE" else "NOT_OBSERVED"
    if t:
        facts["target_fact"] = "CLAIM_WITHOUT_EFFECT" if t.effect_number is None else "CLAIM_WITH_EFFECT"
    if c is None and t and controller.coverage == "COMPLETE" and controller.order >= target.order:
        return Result(status="INCONSISTENT", reason="ORPHAN_TARGET_CLAIM", **facts)
    if c:
        expected = (c.action_id, c.session_id, c.run_id)
        action, approval = c.action, c.approval
        if (action and (action.action_id, action.session_id, action.run_id) != expected
            or approval and (approval.action_id, approval.session_id, approval.run_id, approval.approval_id)
            != (*expected, c.approval_id)):
            return Result(status="INCONSISTENT", reason="CONTROLLER_BINDING_CONFLICT", **facts)
        anchored = bool(action and approval and approval.consumed and approval.decision == "APPROVED")
        event = c.event
        if event and (event.dispatch_id, event.action_id, event.session_id) != (
            c.dispatch_id, c.action_id, c.session_id
        ):
            return Result(status="INCONSISTENT", reason="EVENT_BINDING_CONFLICT", **facts)
        if (c.outcome != (event.status if event else None)
            or (approval and (not approval.consumed or approval.decision != "APPROVED"))
            or (not anchored and controller.coverage == "COMPLETE")):
            return Result(status="INCONSISTENT", reason="CONTROLLER_EVIDENCE_CONFLICT", **facts)
        committed = c.outcome == "SIMULATED_EFFECT_COMMITTED"
        if committed != (c.effect_number is not None) or (committed and c.digest is None):
            return Result(status="INCONSISTENT", reason="CONTROLLER_EFFECT_CONFLICT", **facts)
        if t and (c.action_id != t.action_id or (c.digest is not None and c.digest != t.digest)
                  or c.outcome == "RESERVED_NO_SEND"
                  or (c.effect_number is not None and t.effect_number is not None
                      and c.effect_number != t.effect_number)):
            return Result(status="INCONSISTENT", reason="CROSS_SOURCE_CONFLICT", **facts)
        if t and c.digest is None and controller.coverage == "COMPLETE" and controller.order >= target.order:
            return Result(status="INCONSISTENT", reason="MISSING_PRE_SEND_DIGEST", **facts)
        if committed and target.order >= controller.order and (
            (t is None and target.coverage == "COMPLETE") or (t and t.effect_number is None)
        ):
            return Result(status="INCONSISTENT", reason="LATER_TARGET_EVIDENCE_CONFLICT", **facts)
        if anchored:
            target_effect = t.effect_number if t and c.digest == t.digest else None
            effect = c.effect_number or target_effect
            if effect is not None:
                sources = tuple(source for source, number in
                                (("CONTROLLER", c.effect_number), ("TARGET", target_effect)) if number is not None)
                return Result(status="SIMULATED_EFFECT_RECORDED", reason="BOUND_EFFECT",
                              effect_number=effect, effect_sources=sources, **facts)
            if c.outcome == "RESERVED_NO_SEND":
                return Result(status="CONTROLLER_NO_SEND_RECORDED", reason="BOUND_NO_SEND", **facts)
    if c or t:
        return Result(status="UNRESOLVED", reason="OBSERVED_WITHOUT_BOUND_OUTCOME", **facts)
    if "UNAVAILABLE" in coverage:
        return Result(status="UNAVAILABLE", reason="SOURCE_UNAVAILABLE", **facts)
    if coverage == ("COMPLETE", "COMPLETE"):
        return Result(status="MISSING_EVIDENCE", reason="NO_OBSERVED_ROWS", **facts)
    return Result(status="UNRESOLVED", reason="PARTIAL_COVERAGE", **facts)


def analyze_fixture(data: bytes) -> Result:
    try:
        fixture = decode_fixture(data)
    except ValueError:
        return Result(status="INVALID", reason="INVALID_FIXTURE")
    return reconcile(fixture)


def format_result(result: Result) -> str:
    return f"{result.status} ({result.reason})"
