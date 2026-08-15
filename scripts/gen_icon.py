"""生成 Tauri Windows 资源文件所需的占位图标 src-tauri/icons/icon.ico（256x256 32bit）。

无需 Pillow，纯标准库构造 ICO（BMP 编码）。后续可替换为正式设计稿。
"""

import struct
from pathlib import Path

SIZE = 256
OUT = Path(__file__).resolve().parents[1] / "src-tauri" / "icons" / "icon.ico"


def build_ico() -> bytes:
    # BITMAPINFOHEADER（40B；biHeight 翻倍：ICO 需含 AND 掩码高度）
    header = struct.pack(
        "<IiiHHIIiiII",
        40, SIZE, SIZE * 2, 1, 32, 0, 0, 0, 0, 0, 0,
    )
    # 像素数据（自下而上 BGRA）：渐变蓝底 + 白色圆（记忆镜像占位视觉）
    px = bytearray()
    for y in range(SIZE - 1, -1, -1):
        for x in range(SIZE):
            t = y / (SIZE - 1)
            r, g, b = int(20 + 30 * t), int(80 + 40 * t), 180
            dx, dy = x - SIZE / 2, y - SIZE / 2
            if dx * dx + dy * dy <= 96 * 96:
                r, g, b = 245, 245, 245
            px += bytes((b, g, r, 255))
    # AND 掩码（全 0 = 不透明）
    row_bytes = ((SIZE + 31) // 32) * 4
    mask = b"\x00" * (row_bytes * SIZE)
    image = header + bytes(px) + mask

    icondir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(image), 22)  # width/height 0 = 256
    return icondir + entry + image


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(build_ico())
    print(f"生成占位图标: {OUT} ({OUT.stat().st_size} 字节)")
