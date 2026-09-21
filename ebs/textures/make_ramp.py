"""손잡이 기둥에 물릴 알파 램프 PNG 하나. 바깥 25% 는 1, 거기서 곤두박질친다."""
import struct, zlib, pathlib, sys

WIDE, HIGH, KEEP, POWER = 256, 8, 0.5, 8


def chunk(kind: bytes, body: bytes) -> bytes:
    """길이 + 종류 + 몸통 + CRC"""
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def ramp(at: int) -> int:
    """그 가로 자리의 값. 바깥 KEEP 만큼은 255, 거기서 POWER 제곱으로 떨어진다"""
    step = at / (WIDE - 1)
    away = abs(step * 2.0 - 1.0)
    return round(255 * min(1.0, away / KEEP) ** POWER)


row = b""
for at in range(WIDE):
    one = ramp(at)
    row += bytes((one, one, one, one))
raw = b"".join(b"\x00" + row for _ in range(HIGH))

png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDE, HIGH, 8, 6, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(raw, 9))
       + chunk(b"IEND", b""))

out = pathlib.Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(png)
print(f"{out} {len(png)} bytes, {WIDE}x{HIGH} RGBA")
print("  ".join(f"{at}:{ramp(at)}" for at in (0, 32, 64, 96, 128, 160, 192, 224, 255)))
