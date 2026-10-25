import pickle, re, collections, sys, datetime, bisect, random
import os
SP = os.environ.get('INPUT_ACTIONS_WORK') or os.getcwd()   # packets.pkl lives here
packets = pickle.load(open(SP + '/packets.pkl', 'rb'))
pat = re.compile(rb'\x52(...)(...)(.{12})(.)\x54', re.S)
def t(ts): return datetime.datetime.fromisoformat(ts).timestamp()
timeline = []; hits = []
for ts, seq, msgs in packets:
    tt = t(ts)
    for d, e, c, r, data in msgs:
        if (c, r) == (172, 1):
            ms = pat.findall(data)
            if ms: timeline.append((tt, int.from_bytes(ms[0][0], 'little')))
        elif (c, r) == (115, 4):
            hits.append((tt, data))
times = [x[0] for x in timeline]
# random-window baseline for bit 5 within 1 s
random.seed(1)
t0, t1 = times[0], times[-1]
def any_bit(lo, hi, p):
    a = bisect.bisect_left(times, lo); b = bisect.bisect_right(times, hi)
    return any(timeline[k][1] >> p & 1 for k in range(a, b))
n = 4000
for p in (0, 3, 4, 5):
    hit_rate = sum(any_bit(ht - 1.0, ht, p) for ht, _ in hits) / len(hits)
    # random times restricted to times where we have input data (sample from timeline times)
    rnd = sum(any_bit(tt - 1.0, tt, p) for tt in (random.choice(times) for _ in range(n))) / n
    print('bit %d: P(set in 1s before hit)=%.3f   P(set in 1s before random input frame)=%.3f' % (p, hit_rate, rnd))
# bursts of bit 5
runs = []; cur = None
for tt, v in timeline:
    on = bool(v >> 5 & 1)
    if on and cur is None: cur = [tt, tt]
    elif on: cur[1] = tt
    elif cur is not None and tt - cur[1] > 0.15:
        runs.append(tuple(cur)); cur = None
if cur: runs.append(tuple(cur))
print('bit5 bursts', len(runs))
hit_times = [h[0] for h in hits]
hh = 0; lags = []
for a, b in runs:
    i = bisect.bisect_left(hit_times, a - 0.05); 
    if i < len(hit_times) and hit_times[i] <= b + 0.5:
        hh += 1; lags.append(hit_times[i] - a)
print('bursts with a 115/4 within [start-0.05, end+0.5]:', hh, 'of', len(runs))
lags.sort()
print('lag start->first hit percentiles', [round(lags[int(len(lags) * q)], 3) for q in (0.1, 0.5, 0.9)] if lags else None)
# per-hit: nearest bit5-on frame time before hit
lag2 = []
for ht, _ in hits:
    i = bisect.bisect_right(times, ht) - 1
    while i >= 0 and not (timeline[i][1] >> 5 & 1): i -= 1
    if i >= 0: lag2.append(ht - times[i])
lag2.sort()
print('hit minus last bit5-on frame: n=%d  pct10/50/90/99 = %s' % (len(lag2), [round(lag2[int(len(lag2) * q)], 3) for q in (0.1, 0.5, 0.9, 0.99)]))
# bit 7 (Interact) bursts and bit 17 (UIMap) times
for p in (7, 9, 17, 11, 12, 13, 14):
    ts_ = [tt for tt, v in timeline if v >> p & 1]
    print('bit', p, 'frames(newest)=', len(ts_), 'first/last', (datetime.datetime.fromtimestamp(ts_[0]).isoformat(timespec='seconds'), datetime.datetime.fromtimestamp(ts_[-1]).isoformat(timespec='seconds')) if ts_ else None)
