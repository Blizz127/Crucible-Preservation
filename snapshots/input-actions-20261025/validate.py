import pickle, re, collections
import os
SP = os.environ.get('INPUT_ACTIONS_WORK') or os.getcwd()   # packets.pkl lives here
packets = pickle.load(open(SP + '/packets.pkl', 'rb'))
hdr = collections.Counter(); off52 = collections.Counter(); mv = [collections.Counter() for _ in range(3)]
lat = collections.Counter(); ftail = collections.Counter(); n326 = 0
cfo = collections.Counter(); mult = collections.Counter(); tail_ok = 0
pm_off = collections.Counter(); same_len = collections.Counter()
for ts, seq, msgs in packets:
    for d, e, c, r, data in msgs:
        if (c, r) != (172, 1) or len(data) != 326: continue
        n326 += 1
        for k in range(3):
            f = data[106 * k:106 * (k + 1)]
            hdr[f[6:14].hex()] += 1
            cfo[f[4]] += 1; mult[f[5]] += 1
            i = f.find(b'\x52', 14)
            # locate the 0x52 type byte that is followed by 3+3+8+4+1 bytes and then 0x54
            for j in range(14, 70):
                if f[j] == 0x52 and f[j + 20] == 0x54:
                    off52[j] += 1
                    for a in range(3): mv[a][f[j + 4 + a]] += 1
                    lat[f[j + 19]] += 1
                    # PlayerMovement: 0x6e right after 0x54 (+optional payload byte)
                    q = f.find(b'\x6e', j + 21)
                    pm_off[q - j] += 1
                    break
print('326-byte messages', n326)
print('frame bytes +6..+13 (top):', hdr.most_common(5))
print('ClientFrameOffset', cfo, 'ServerFrameMultiplier', mult)
print('offset of 0x52 within frame:', off52.most_common(8))
print('moveX', mv[0].most_common(6)); print('moveY', mv[1].most_common(6)); print('moveZ', mv[2].most_common(6))
print('latentAction', lat.most_common(8))
print('0x6e offset after 0x52:', pm_off.most_common(6))
