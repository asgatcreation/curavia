#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import contextlib
import os
import sys


def _force_utf8_output() -> None:
    """
    Make stdout and stderr UTF-8 before Django writes anything.

    Windows consoles still default to cp1252, which cannot encode the naira
    sign — so `manage.py seed_platform` died with a UnicodeEncodeError on
    U+20A6 while printing a plan's price. For a system whose every amount is
    in naira, that is not a cosmetic problem: it would hit any command that
    prints money, on the machine this is developed on.

    Fixed here rather than per-command, and before `django.setup()`, so every
    management command inherits it. `reconfigure` exists on Python 3.7+; the
    guard is for the case where stdout has been replaced by something that is
    not a TextIOWrapper, such as a test harness capturing output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # Already-closed or replaced streams are not worth failing over.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


def main() -> None:
    _force_utf8_output()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError("Couldn't import Django. Is the virtual environment activated?") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
