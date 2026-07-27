import importlib
mods=['MetaTrader5','requests','pandas','numpy','matplotlib']
for m in mods:
    try:
        importlib.import_module(m)
        print(m+': OK')
    except Exception as e:
        print(m+': FAIL -> '+repr(e))
