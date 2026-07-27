import sqlite3

conn = sqlite3.connect('trades.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print('signals:', cur.execute('select count(*) as c from signals').fetchone()['c'])
print('executions:', cur.execute('select count(*) as c from executions').fetchone()['c'])
print('closures:', cur.execute('select count(*) as c from closures').fetchone()['c'])
print()
print('All zone-type results:')
for row in cur.execute("""
    select s.zone_type, count(*) as cnt,
           sum(case when c.pnl > 0 then 1 else 0 end) as wins,
           round(avg(c.pnl), 4) as avg_pnl,
           round(avg(c.pnl_r), 4) as avg_pnl_r
    from signals s
    join executions e on e.signal_id = s.id
    join closures c on c.ticket = e.ticket
    group by s.zone_type
    order by cnt desc
"""):
    print(dict(row))

print()
print('Premium vs discount only:')
for row in cur.execute("""
    select s.zone_type, count(*) as cnt,
           sum(case when c.pnl > 0 then 1 else 0 end) as wins,
           round(avg(c.pnl), 4) as avg_pnl,
           round(avg(c.pnl_r), 4) as avg_pnl_r
    from signals s
    join executions e on e.signal_id = s.id
    join closures c on c.ticket = e.ticket
    where s.zone_type in ('premium_zone', 'discount_zone')
    group by s.zone_type
    order by s.zone_type
"""):
    print(dict(row))

print()
print('Latest closed trades:')
for row in cur.execute("""
    select s.symbol, s.zone_type, s.direction, c.outcome, c.pnl, c.pnl_r, c.closed_at
    from signals s
    join executions e on e.signal_id = s.id
    join closures c on c.ticket = e.ticket
    order by c.closed_at desc
    limit 10
"""):
    print(dict(row))
