"""QuickXorHash, the content hash SharePoint Online and OneDrive report for every file.

Computing it locally lets the uploader confirm that the copy stored in SharePoint is
byte-for-byte the attachment it read from the mailbox, without downloading the file again.
The algorithm XORs each byte into a 160-bit register at bit offset (11 * index) mod 160,
then XORs the 64-bit little-endian file length into the last 8 bytes.
"""
from __future__ import annotations

import base64

import numpy as np

WIDTH = 160
SHIFT = 11
_MASK = (1 << WIDTH) - 1


def quickxorhash(data: bytes) -> str:
    """Return the base64 QuickXorHash of ``data`` (standard alphabet, as Graph reports it)."""
    n = len(data)
    reg = 0
    if n:
        buf = np.frombuffer(data, dtype=np.uint8)
        pad = (-n) % WIDTH
        if pad:
            buf = np.concatenate([buf, np.zeros(pad, dtype=np.uint8)])
        # bytes whose index differs by a multiple of 160 land on the same bit offset
        folded = np.bitwise_xor.reduce(buf.reshape(-1, WIDTH), axis=0)
        for i, b in enumerate(folded.tolist()):
            if b:
                off = (i * SHIFT) % WIDTH
                reg ^= ((b << off) | (b >> (WIDTH - off))) & _MASK
    out = bytearray(reg.to_bytes(WIDTH // 8, "little"))
    for i, b in enumerate(n.to_bytes(8, "little")):
        out[WIDTH // 8 - 8 + i] ^= b
    return base64.b64encode(bytes(out)).decode()
