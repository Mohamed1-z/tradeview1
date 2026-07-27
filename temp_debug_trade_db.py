from pathlib import Path
from swift_scanner.trade_db import TradeDB
from swift_scanner.models import Zone, SignalEvent
from datetime import datetime, timezone
import os
import sqlite3

p = Path('C:/Users/LENOVO/AppData/Local/Temp/test_trade_db.db')
if p.exists():
    os.unlink(p)

zone = Zone(
    zone_type='premium_zone',
    direction='bullish',
    symbol_request='EURUSD',
    symbol_actual='EURUSD',
    created_at=datetime.now(timezone.utc),
    left_index=0,
    right_index=0,
    top=1.12,
    bottom=1.08,
    formed_index=1,
)

event = SignalEvent(
    symbol_request='EURUSD',
    symbol_actual='EURUSD',
    timeframe='M15',
    triggered_at=datetime.now(timezone.utc),
    price=1.1,
    zone=zone,
)

print('zone_type', event.zone.zone_type)
print('signal_source', event.signal_source)
print('signal_label', event.signal_label)

if p.exists():
    os.unlink(p)

if p.exists():
    print('file exists already')


db = TradeDB(db_path=p)

conn = sqlite3.connect(p)
cur = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
print('tables:')
for row in cur.fetchall():
    print(row)

for ticket, pnl in enumerate([0.02, -0.01, -0.02, -0.01, -0.01], start=1):
    signal_id = db.record_signal(event)
    print('signal_id', signal_id)
    db.record_execution(ticket, event, 0.10, signal_id=signal_id)
    db.record_close(ticket, 1.10 + pnl, 'TP_FINAL', pnl, None)

stats = db.get_pattern_stats(event.symbol_actual, event.timeframe, event.zone.direction, event.zone.zone_type)
print('stats', stats)

conn = sqlite3.connect(p)
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT * FROM signals').fetchall()
print('signals rows', [dict(r) for r in row])
row = conn.execute('SELECT * FROM executions').fetchall()
print('executions rows', [dict(r) for r in row])
row = conn.execute('SELECT * FROM closures').fetchall()
print('closures rows', [dict(r) for r in row])
conn.close()
