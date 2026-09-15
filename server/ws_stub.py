"""WebSocket frame decoding for caller-provided streams."""

import struct


def recv_exact(conn, n):
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('eof')
        buf += chunk
    return buf


def read_frame(conn):
    hdr = recv_exact(conn, 2)
    fin, opcode, masked, ln = (hdr[0] >> 7, hdr[0] & 15, hdr[1] >> 7, hdr[1] & 127)
    if ln == 126:
        ln = struct.unpack('>H', recv_exact(conn, 2))[0]
    elif ln == 127:
        ln = struct.unpack('>Q', recv_exact(conn, 8))[0]
    mask = recv_exact(conn, 4) if masked else None
    payload = recv_exact(conn, ln) if ln else b''
    if masked:
        payload = bytes((b ^ mask[i % 4] for i, b in enumerate(payload)))
    return (fin, opcode, payload)
