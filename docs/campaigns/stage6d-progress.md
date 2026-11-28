# Stage 6.D campaign

The owner authorized bounded engineering from verified main
`be0f0237725068c4cedc19e713c197297e6ee5fe`. Production remains **NO-GO**.
Formal acceptance remains with the owner.

The independent read-only architecture reviewer rejected the default
PID namespace because it hides the native Controller PID. The reviewer
recommended proceeding with a dedicated private shared PID namespace
subject to real OS validation. See [ADR-0003](../decisions/ADR-0003-stage6d-docker-portability.md)
and the [threat model delta](../tasks/stage6d/threat-model.md).

Authorized disposable Docker workflow runs at the design checkpoint: **0/4 used**.
