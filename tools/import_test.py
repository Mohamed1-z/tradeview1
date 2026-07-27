"""Attempt to import each top-level module in swift_scanner and app.py to surface import/runtime errors."""
import importlib
import pkgutil
import sys

mods = ["app"]
for finder, name, ispkg in pkgutil.iter_modules(["swift_scanner"]):
    mods.append("swift_scanner." + name)

errors = {}
for m in mods:
    try:
        importlib.import_module(m)
        print(f"{m}: OK")
    except Exception as e:
        errors[m] = repr(e)
        print(f"{m}: FAIL -> {e}")

if errors:
    print("\nSummary of import failures:")
    for k,v in errors.items():
        print(k+": "+v)
