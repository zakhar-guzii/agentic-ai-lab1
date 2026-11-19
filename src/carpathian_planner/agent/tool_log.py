"""The tool-call log on stderr: one line per call, keys redacted (design D4)."""

import json
import logging
import os

SECRET_VARS = ("OPENROUTER_API_KEY", "OWM_API_KEY")

log = logging.getLogger(__name__)


def redact(text: str) -> str:
    """Replace key values with the variable name. Arguments never carry keys; this is a second guard."""
    for var in SECRET_VARS:
        value = os.environ.get(var)
        if value and len(value) >= 8:
            text = text.replace(value, f"<{var}>")
    return text


def _json(value, indent=None) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, default=str)


def log_call(server: str, tool: str, args: dict, status: str, details=None, caller: str = "model") -> None:
    """`[<server>] <tool> caller=<model|code> args=<json> -> <status>`; -v adds the full result below.

    caller="code" marks the calls code makes itself: the forecasts and check_weather_warnings."""
    log.info(redact(f"[{server}] {tool} caller={caller} args={_json(args)} -> {status}"))
    if details is not None:
        log.debug(redact(details if isinstance(details, str) else _json(details, indent=2)))
