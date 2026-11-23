"""Run an operator-invoked Task 4.1 handshake using preconfigured identities.

Invoke as `python -m scripts.stage4_peer_probe --help` from the repository.
Start the listener under the configured server UID, then start the connector
under the configured client UID within ten seconds. The flags are trusted
launch inputs and should come from protected configuration.
"""

import argparse

from adaptive_agentops.stage4_boundary import (
    BoundaryError,
    Endpoint,
    Link,
    TrustedUIDs,
    handshake,
    serve_once,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("listen", "connect"))
    parser.add_argument("--link", required=True, choices=[link.value for link in Link])
    parser.add_argument("--socket", required=True, help="absolute path in a preprovisioned directory")
    parser.add_argument("--access-gid", required=True, type=int)
    parser.add_argument("--agent-uid", required=True, type=int)
    parser.add_argument("--control-uid", required=True, type=int)
    parser.add_argument("--target-uid", required=True, type=int)
    args = parser.parse_args(argv)

    try:
        endpoint = Endpoint(Link(args.link), args.socket, args.access_gid)
        uids = TrustedUIDs(args.agent_uid, args.control_uid, args.target_uid)
        exchange = serve_once if args.mode == "listen" else handshake
        exchange(endpoint, uids)
    except (BoundaryError, OSError, ValueError) as exc:
        parser.exit(1, f"Stage 4.1 handshake failed ({type(exc).__name__})\n")
    print("Stage 4.1 handshake verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
