"""Local MVP operator workflow. A terminal is not proof of human identity.

Requires an existing initialized database. This records decisions only; it does
not grant dispatch rights, consume approvals, or execute tools. Exit codes:
0 = completed, 1 = operation failed, 2 = invalid input, 130 = cancelled.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import UUID

from adaptive_agentops.actions import get_pending_restart_action
from adaptive_agentops.approvals import ApprovalDecision, record_operator_decision


def _open_database(path: str, read_only: bool) -> sqlite3.Connection:
    mode = "ro" if read_only else "rw"
    return sqlite3.connect(Path(path).resolve().as_uri() + f"?mode={mode}", uri=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--db", required=True)
    parser.add_argument("command", choices=("review", "decide"))
    parser.add_argument("action_id")
    args = parser.parse_args(argv)
    try:
        action_id = UUID(args.action_id)
        if action_id.version != 4:
            raise ValueError
    except ValueError:
        print("Invalid UUID4 action ID.", file=sys.stderr)
        return 2
    if args.command == "decide" and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Decision entry requires an interactive terminal.", file=sys.stderr)
        return 2

    try:
        with closing(_open_database(args.db, args.command == "review")) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('actions', 'approvals')"
            )}
            if tables != {"actions", "approvals"}:
                raise ValueError
            # Verify required approval columns without creating or altering schema.
            conn.execute("""
                SELECT approval_id, action_id, run_id, requester_id, agent_id,
                       action_fingerprint, approver_id, decision, created_at,
                       expires_at, consumed FROM approvals LIMIT 0
            """)
            action = get_pending_restart_action(conn, action_id)
            if action is None:
                raise ValueError
            expired = datetime.now(timezone.utc) >= action.expires_at
            print(json.dumps({**action.model_dump(mode="json"), "expired": expired}, indent=2))
            if args.command == "review":
                return 0
            if expired or conn.execute(
                "SELECT 1 FROM approvals WHERE action_id=?", (str(action_id),)
            ).fetchone() is not None:
                print("Action is expired or already decided.", file=sys.stderr)
                return 1

            confirmation = input(
                f"Enter APPROVE {action_id} or REJECT {action_id}: "
            )
            choices = {
                f"APPROVE {action_id}": ApprovalDecision.APPROVED,
                f"REJECT {action_id}": ApprovalDecision.REJECTED,
            }
            if confirmation not in choices:
                print("Confirmation did not match; no decision recorded.", file=sys.stderr)
                return 2

            owned = False
            try:
                if conn.in_transaction:
                    raise ValueError
                conn.execute("BEGIN IMMEDIATE")
                owned = True
                if get_pending_restart_action(conn, action_id) != action:
                    raise ValueError
                approval = record_operator_decision(conn, action_id, choices[confirmation])
                conn.commit()
                owned = False
            except (Exception, KeyboardInterrupt):
                if owned:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                raise
            print(f"Decision committed: {approval.decision.value} for {action_id}.")
            return 0
    except (EOFError, KeyboardInterrupt):
        print("Operation cancelled; no successful decision confirmed.", file=sys.stderr)
        return 130
    except Exception:
        print("Operation failed; no successful decision confirmed. Review stored state before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
