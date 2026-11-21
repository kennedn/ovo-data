"""Read the My Electric Meter Android app's local HTTP endpoint."""

import argparse
from datetime import datetime, timezone
import json
import math
import re
import sys
import time

import requests


def read_watts(session, url, timeout):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    value = data.get("kw") if isinstance(data, dict) else None
    # The app calls this field 'kw', but it contains integer watts.
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError(f"invalid watt reading: {value!r}")
    return int(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("host", metavar="HOST", help="Reader phone hostname or IP address")
    parser.add_argument("-w", "--watch", action="store_true", help="Continuously poll readings")
    parser.add_argument("-i", "--interval", type=float, default=1, metavar="SECONDS",
                        help="Seconds between polls (default: 1)")
    parser.add_argument("-t", "--timeout", type=float, default=5, metavar="SECONDS",
                        help="HTTP timeout in seconds (default: 5)")
    args = parser.parse_args()
    for name in ("interval", "timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"{name} must be positive and finite")
    url = f"http://{args.host}:8844/getData"
    try:
        with requests.Session() as session:
            session.trust_env = False  # Connect directly to the reader on the LAN.
            while True:
                watts = read_watts(session, url, args.timeout)
                print(json.dumps({"watts": watts, "kilowatts": watts / 1000,
                                  "received_at": datetime.now(timezone.utc).isoformat()}),
                      flush=True)
                if not args.watch:
                    break
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    except requests.HTTPError as exc:
        print(f"{url}: HTTP {exc.response.status_code}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, ValueError) as exc:
        print(f"{url}: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
