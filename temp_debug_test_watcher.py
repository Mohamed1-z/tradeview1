import importlib.util
import sys
from pathlib import Path
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location('test_watcher', Path('tests/test_watcher.py'))
module = importlib.util.module_from_spec(spec)
sys.modules['test_watcher'] = module
spec.loader.exec_module(module)

from swift_scanner.config import ScannerConfig
from swift_scanner.trade_db import TradeDB
from swift_scanner.watcher import SwiftWatcher

print('module loaded', module)

path = Path('C:/Users/LENOVO/AppData/Local/Temp/test_trade_db3.db')
if path.exists():
    path.unlink()

make_event = module.make_event

event = make_event('EURUSD', 'M15', 'bullish', 'Main Long Entry')
event.zone.zone_type = 'premium_zone'

print('event zone_type', event.zone.zone_type)
print('signal_label', event.signal_label)

# create db and record

db = TradeDB(db_path=path)
for ticket, pnl in enumerate([0.02, -0.01, -0.02, -0.01, -0.01], start=1):
    signal_id = db.record_signal(event)
    print('signal_id', signal_id)
    db.record_execution(ticket, event, 0.10, signal_id=signal_id)
    db.record_close(ticket, 1.10 + pnl, 'TP_FINAL', pnl, None)

stats = db.get_pattern_stats(event.symbol_actual, event.timeframe, event.zone.direction, event.zone.zone_type)
print('stats', stats)

watcher = SwiftWatcher(ScannerConfig(output_dir=Path('.')))
watcher.trade_db = db
watcher._annotate_event_history(event)
print('lesson', event.lesson)
print('decision_reason', event.decision_reason)
