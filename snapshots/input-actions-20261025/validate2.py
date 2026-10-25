import pickle, re, collections, datetime, bisect
import os
SP = os.environ.get('INPUT_ACTIONS_WORK') or os.getcwd()   # packets.pkl lives here
packets = pickle.load(open(SP + '/packets.pkl', 'rb'))
off = collections.Counter()
for ts, seq, msgs in packets:
    for d, e, c, r, data in msgs:
        if (c, r) != (172, 1) or len(data) != 326: continue
        for k in range(3):
            f = data[106 * k:106 * (k + 1)]
            for j in range(14, 70):
                if f[j] == 0x52 and f[j + 20] == 0x54:
                    off[(k, j)] += 1
                    break
print('0x52 offset by (frame k, offset):', sorted(off.items()))
# the ~5% hits without fire bit: which bits are set in the preceding second?
pat = re.compile(rb'\x52(...)(...)(.{12})(.)\x54', re.S)
def t(ts): return datetime.datetime.fromisoformat(ts).timestamp()
timeline = []; hits = []
nonstd_bits = collections.Counter(); nonstd_n = collections.Counter(); sizes_with_bits = collections.Counter()
for ts, seq, msgs in packets:
    tt = t(ts)
    for d, e, c, r, data in msgs:
        if (c, r) == (172, 1):
            ms = pat.findall(data)
            if len(data) == 326 and ms: timeline.append((tt, int.from_bytes(ms[0][0], 'little')))
            if len(data) != 326:
                nonstd_n[len(data)] += 1
                for m in ms:
                    v = int.from_bytes(m[0], 'little')
                    nonstd_bits[v] += 1
        elif (c, r) == (115, 4):
            hits.append(tt)
times = [x[0] for x in timeline]
miss = []
for ht in hits:
    a = bisect.bisect_left(times, ht - 1.0); b = bisect.bisect_right(times, ht)
    v = 0
    for k in range(a, b): v |= timeline[k][1]
    if not (v >> 5 & 1): miss.append((ht, v))
print('hits without fire bit in preceding 1s:', len(miss), 'of', len(hits))
cnt = collections.Counter()
for ht, v in miss:
    for p in range(18):
        if v >> p & 1: cnt[p] += 1
    if v == 0: cnt['none'] += 1
print('bits set in those windows:', dict(cnt))
print('non-326 input messages by size (top):', nonstd_n.most_common(8), 'total', sum(nonstd_n.values()))
print('non-326 messages: momentaryBits values found by 0x52..0x54 locator:', nonstd_bits.most_common(10))
