"""Native platform boundary. Both local dependencies travel inside this package."""

from pathlib import Path
from runpy import run_path

from app.contracts import CapabilityOutput
from app.errors import PlatformError

_base = Path(__file__).resolve().parent
_sdk = run_path(str(_base / "_module_kit.py"))
_business = run_path(str(_base / "business.py"))


def analyze(ctx, payload):
    try:
        result = _sdk["run_tool"](
            ctx, permission="__MODULE_ID__.use", payload=payload, handler=_business["analyze"]
        )
    except _sdk["KitError"] as error:
        raise PlatformError(error.code, error.message, error.status) from None
    return CapabilityOutput(summary=result["summary"], data=result["data"])
