# -*- coding: utf-8 -*-
"""Shared crash reporting for the Jenness Enterprises tools.

When a tool hits an unexpected error, ``report()`` assembles a detailed report
(what failed, where in the code, and pertinent context), saves it to a file, and
shows it in the tool messages with instructions to email it to the author so the
problem can be fixed. Call it from a tool's ``except`` block; it never raises.
"""

import os
import sys
import tempfile
import traceback
from datetime import datetime

import arcpy

SUPPORT_EMAIL = "jeffj@jennessent.com"


def _describe_parameters(parameters):
    if not parameters:
        return "    (none)"
    lines = []
    for p in parameters:
        try:
            lines.append("    {} = {}".format(p.name, p.valueAsText))
        except Exception:
            lines.append("    <parameter> = <unavailable>")
    return "\n".join(lines)


def build_report(tool_label, parameters=None):
    """Assemble the report string for the exception currently being handled."""
    exc_type, exc_value, _ = sys.exc_info()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        pro_version = arcpy.GetInstallInfo().get("Version", "unknown")
    except Exception:
        pro_version = "unknown"
    try:
        gp_messages = arcpy.GetMessages(2)      # geoprocessing error messages
    except Exception:
        gp_messages = ""

    parts = [
        "=" * 72,
        "Jenness Enterprises Tools - Error Report",
        "Please email this report to {} so it can be fixed.".format(SUPPORT_EMAIL),
        "=" * 72,
        "Tool:          {}".format(tool_label),
        "Date / time:   {}".format(now),
        "ArcGIS Pro:    {}".format(pro_version),
        "Python:        {}".format(sys.version.split()[0]),
        "",
        "Error type:    {}".format(exc_type.__name__ if exc_type else "unknown"),
        "Error message: {}".format(exc_value),
        "",
        "Parameters used:",
        _describe_parameters(parameters),
        "",
        "Where it happened (most recent call last):",
        traceback.format_exc().rstrip(),
    ]
    if gp_messages:
        parts += ["", "Geoprocessing messages:", gp_messages.rstrip()]
    parts += ["=" * 72]
    return "\n".join(parts)


def report(messages, tool_label, parameters=None):
    """Build the report, save it to a file, and show it with email instructions.

    Returns the report file path (or None if it could not be written). Never raises.
    """
    try:
        text = build_report(tool_label, parameters)
    except Exception:
        text = "Error report could not be fully assembled.\n" + traceback.format_exc()

    path = None
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() else "_" for c in str(tool_label))[:40]
        path = os.path.join(tempfile.gettempdir(),
                            "JennessTools_Error_{}_{}.txt".format(safe, stamp))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        path = None

    if messages is not None:
        intro = ("This tool stopped because of an unexpected error. Please email the "
                 "report below to {email} so it can be fixed".format(email=SUPPORT_EMAIL))
        intro += (" (a copy was saved to {}).".format(path) if path
                  else " (the report could not be saved to a file; please copy it from here).")
        try:
            messages.addErrorMessage(intro)
            messages.addErrorMessage(text)
        except Exception:
            pass
    return path
