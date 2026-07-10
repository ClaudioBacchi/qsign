"""Capture one Wacom STU signature and save the SVG to disk."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.wacom.stu_sdk import WacomSTUSDK, WacomSTUSDKError


def main() -> int:
    output = Path("dist") / "wacom_signature_test.svg"
    print("Firma sulla tavoletta Wacom. La cattura dura al massimo 20 secondi.")
    try:
        signature = WacomSTUSDK().capture_signature(max_seconds=20.0)
    except WacomSTUSDKError as error:
        print(f"ERROR: {error}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(signature.content)
    print(f"Firma acquisita: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
