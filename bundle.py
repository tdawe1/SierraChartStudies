#!/usr/bin/env python3
"""Bundle self-contained studies into one .cpp for a single Remote Build.

Sierra Chart builds one DLL per build, but one DLL can hold many studies.
This merges each source (minus its own SCDLLName) into its own namespace
and adds a global forwarder per scsf_ entry point.

  python3 bundle.py --out AllStudies.cpp Orion.cpp FlipperStudies.cpp ...
  python3 bundle.py --install        # bundle defaults + copy all to ACS_Source

Rules for sources: self-contained, no #define macros, no colliding
global (non-static, non-namespaced) helpers. scsf_ functions keep their
exact names via forwarders so chart study names are unchanged.
"""
import argparse
import os
import re
import shutil
from pathlib import Path

DEFAULT_SOURCES = [
    "Orion.cpp",
    "FlipperStudies.cpp",
    "SatyPivotRibbon.cpp",
    "DiscordAlerts.cpp",
    "InitialBalanceStatistics.cpp",
    "PropRiskOverlay.cpp",
    "OrionExecutor.cpp",
    "backtest/exporter/BacktestExporter.cpp",
]

DEFAULT_ACS = os.path.expanduser(
    os.environ.get("SC_ACS_SOURCE", "~/.wine/drive_c/SierraChart/ACS_Source")
)

SCSF_RE = re.compile(r"SCSFExport\s+(scsf_\w+)\s*\(\s*SCStudyInterfaceRef\s+\w+\s*\)")


def bundle(sources, dll_name):
    includes = []
    seen = set()
    chunks = []
    forwards = []
    for src in sources:
        stem = Path(src).stem
        ns = "bundle_" + re.sub(r"\W", "_", stem)
        body = []
        for line in Path(src).read_text().splitlines():
            s = line.strip()
            if re.match(r'#\s*include\s*"sierrachart\.h"', s):
                continue
            if re.match(r"#\s*include\s*[<\"]", s):
                if s not in seen:
                    seen.add(s)
                    includes.append(s)
                continue
            if re.match(r"SCDLLName\s*\(", s):
                continue
            # SCSFExport is extern "C": flat global name even in a namespace.
            # Demote bundled copies to plain C++ so only the forwarders export.
            line = re.sub(r"SCSFExport\s+(scsf_\w+)", r"void \1", line)
            body.append(line)
        for m in SCSF_RE.finditer(Path(src).read_text()):
            forwards.append((m.group(1), ns))
        chunks.append("namespace %s {\n%s\n}  // namespace %s" % (ns, "\n".join(body), ns))
    parts = ['#include "sierrachart.h"', *includes, "", 'SCDLLName("%s")' % dll_name, ""]
    parts.extend(chunks)
    parts.append("")
    for fn, ns in forwards:
        parts.append("SCSFExport %s(SCStudyInterfaceRef sc) { %s::%s(sc); }" % (fn, ns, fn))
    return "\n".join(parts) + "\n", forwards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="AllStudies.cpp")
    ap.add_argument("--name", default="All Studies")
    ap.add_argument("--install", action="store_true",
                    help="copy sources + bundle into ACS_Source")
    ap.add_argument("--install-dir", default=DEFAULT_ACS)
    ap.add_argument("sources", nargs="*")
    a = ap.parse_args()
    sources = a.sources or DEFAULT_SOURCES
    text, forwards = bundle(sources, a.name)
    Path(a.out).write_text(text)
    print("bundled %d files, %d studies -> %s" % (len(sources), len(forwards), a.out))
    for fn, _ in forwards:
        print("  " + fn)
    if a.install:
        dest = Path(a.install_dir)
        dest.mkdir(parents=True, exist_ok=True)
        for src in sources:
            shutil.copy(src, dest / Path(src).name)
        shutil.copy(a.out, dest / Path(a.out).name)
        print("installed %d sources + bundle -> %s" % (len(sources), dest))


if __name__ == "__main__":
    main()
