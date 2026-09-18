"""Standalone scheduling and batching helpers."""


def encode_varint(value):
    if value < 0:
        raise ValueError('varint is unsigned; zigzag first')
    out = bytearray()
    while True:
        b = value & 127
        value >>= 7
        if value:
            out.append(b | 128)
        else:
            out.append(b)
            return bytes(out)
