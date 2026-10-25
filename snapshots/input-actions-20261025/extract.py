"""Extract client EntityRpcs (172/1 input, 115/4 hit reports) from the big UDP log into a pickle.
The log's `hex:` line is the raw LZ4 datagram (first 1024 bytes); decompress, check CRC, then parse like observe_client_rpcs."""
import re, struct, sys, pickle, collections, zlib
import lz4.block
import os
SP = os.environ.get('INPUT_ACTIONS_WORK') or os.getcwd()   # packets.pkl lives here
src = sys.argv[1] if len(sys.argv) > 1 else SP + '/shard_udp_big.log'
ts_re = re.compile(r'^\[(\d{4}-\d\d-\d\dT[\d:.]+)\]')
def parse_rpcs(payload):
    if len(payload) < 2: raise ValueError
    count = int.from_bytes(payload[:2], 'big')
    if count > 2048: raise ValueError
    off, out = 2, []
    for _ in range(count):
        if len(payload) - off < 9: raise ValueError
        d, e, c, r, s = struct.unpack_from('>BIBBH', payload, off)
        off += 9
        if d > 4 or s > 6144 or s > len(payload) - off: raise ValueError
        out.append((d, e, c, r, payload[off:off+s])); off += s
    if off != len(payload): raise ValueError
    return out
packets = []
hist = collections.Counter()
want_next_hex = False
cur_ts = None
bad = collections.Counter()
nlines = 0
with open(src, 'r', errors='replace') as fh:
    for line in fh:
        if 'PacketType=11 (EntityRpcs)' in line:
            want_next_hex = True
            m = ts_re.match(line)
            cur_ts = m.group(1) if m else None
            continue
        if want_next_hex and 'hex: ' in line:
            want_next_hex = False
            nlines += 1
            hx = line.split('hex: ', 1)[1].strip()
            try:
                wire = bytes.fromhex(hx)
            except ValueError:
                bad['hex'] += 1; continue
            try:
                raw = lz4.block.decompress(wire, uncompressed_size=4096)
            except Exception:
                bad['lz4'] += 1; continue
            region, crc = raw[:-4], raw[-4:]
            if zlib.crc32(region) != int.from_bytes(crc, 'big'):
                bad['crc'] += 1; continue
            if len(region) < 13: bad['short'] += 1; continue
            reliable = bool(region[10] & 1)
            off = 13 if reliable else 11
            try:
                msgs = parse_rpcs(region[off:])
            except Exception:
                bad['rpc'] += 1; continue
            seq = int.from_bytes(region[11:13] if reliable else region[2:4], 'big')
            for d, e, c, r, data in msgs:
                hist[(e, c, r)] += 1
            packets.append((cur_ts, seq, [(d, e, c, r, data) for d, e, c, r, data in msgs if (c, r) in ((172, 1), (115, 4))]))
print('hex lines', nlines, 'packets', len(packets), 'bad', dict(bad))
for k, v in sorted(hist.items(), key=lambda kv: -kv[1])[:25]:
    print(hex(k[0]), k[1], k[2], v)
pickle.dump(packets, open(SP + '/packets.pkl', 'wb'))
