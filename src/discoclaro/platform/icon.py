"""Generador puro en Python del icono oficial `discoclaro.ico` (Windows 10/11).

Construye un archivo `.ico` multi-resolución (256×256, 64×64, 32×32) con
frames PNG RGBA embebidos (estándar de Windows desde Vista/10/11), sin
necesitar Pillow ni librerías externas. El diseño combina el emblema de disco y
terminal (`>_`) sobre el degradado ámbar-cálido (`#ff622e`) y obsidiana
(`#0f131d`) de la marca discoclaro.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


def _pack_png_rgba(width: int, height: int, rgba_bytes: bytes) -> bytes:
    """Empaqueta un buffer RGBA crudo en un archivo PNG válido en memoria."""

    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw_rows = bytearray()
    stride = width * 4
    for y in range(height):
        raw_rows.append(0)  # filtro None
        start = y * stride
        raw_rows.extend(rgba_bytes[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def _sdf_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Distancia euclidiana mínima del punto `(px, py)` al segmento `A -> B`."""
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    c1 = wx * vx + wy * vy
    if c1 <= 0.0:
        return math.hypot(px - ax, py - ay)
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return math.hypot(px - bx, py - by)
    t = c1 / c2
    proj_x = ax + t * vx
    proj_y = ay + t * vy
    return math.hypot(px - proj_x, py - proj_y)


def _render_icon_rgba(size: int) -> bytes:
    """Renderiza el icono oficial de discoclaro a `size × size` píxeles con
    antialiasing por distancia firmada (SDF):
    - Fondo squircle redondeado con degradado ámbar cálido (#ff622e) e índigo (#1e1b4b)
    - Anillo medidor de disco inteligente a la derecha
    - Símbolo `>_` nítido en blanco puro en el centro-izquierda
    """
    buf = bytearray(size * size * 4)
    inv = 1.0 / size
    aa = 1.25 / size

    for y in range(size):
        ny = (y + 0.5) * inv  # 0..1
        for x in range(size):
            nx = (x + 0.5) * inv  # 0..1

            # 1. Squircle / rectángulo redondeado exterior (margen 0.05, radio 0.22)
            cx = abs(nx - 0.5) - (0.45 - 0.22)
            cy = abs(ny - 0.5) - (0.45 - 0.22)
            out_x = cx if cx > 0.0 else 0.0
            out_y = cy if cy > 0.0 else 0.0
            in_dist = min(max(cx, cy), 0.0)
            dist_box = math.hypot(out_x, out_y) + in_dist - 0.22

            if dist_box > aa:
                continue  # transparente fuera del icono

            alpha_cov = max(0.0, min(1.0, 0.5 - dist_box / (2.0 * aa)))

            # 2. Degradado diagonal de marca: Ámbar cálido (#ff622e) -> Obsidiana/Índigo
            t_grad = max(0.0, min(1.0, (nx * 0.55 + ny * 0.45)))
            # Brillo radial cálido arriba a la izquierda
            glow = math.exp(-((nx - 0.24) ** 2 + (ny - 0.22) ** 2) * 3.8)
            base_r = 45 * (1.0 - t_grad) + 18 * t_grad
            base_g = 32 * (1.0 - t_grad) + 22 * t_grad
            base_b = 65 * (1.0 - t_grad) + 52 * t_grad
            r = int(24 + 231 * glow + (1.0 - glow) * base_r)
            g = int(28 + 78 * glow + (1.0 - glow) * base_g)
            b = int(46 + 18 * glow + (1.0 - glow) * base_b)

            # Borde interior sutil iluminado (glass highlight)
            border_band = abs(dist_box + 0.012)
            if border_band < 0.014:
                bt = (1.0 - border_band / 0.014) * 0.42
                r = int(r * (1.0 - bt) + 255 * bt)
                g = int(g * (1.0 - bt) + 180 * bt)
                b = int(b * (1.0 - bt) + 140 * bt)

            # 3. Anillo de disco / gauge circular alrededor del centro (radio 0.31)
            rc_dist = abs(math.hypot(nx - 0.5, ny - 0.5) - 0.32)
            if rc_dist < 0.032:
                angle = math.atan2(ny - 0.5, nx - 0.5)
                ring_cov = max(0.0, min(1.0, (0.032 - rc_dist) / max(aa, 0.012)))
                # Arco activo ámbar-blanco (75% del disco) vs pista translúcida
                if angle > -2.4 and angle < 2.1:
                    rr, rg, rb = 255, 138, 76
                    mix = ring_cov * 0.85
                else:
                    rr, rg, rb = 148, 163, 184
                    mix = ring_cov * 0.28
                r = int(r * (1.0 - mix) + rr * mix)
                g = int(g * (1.0 - mix) + rg * mix)
                b = int(b * (1.0 - mix) + rb * mix)

            # 4. Emblema central `>_` en blanco puro
            d_chev1 = _sdf_segment(nx, ny, 0.31, 0.37, 0.46, 0.49)
            d_chev2 = _sdf_segment(nx, ny, 0.46, 0.49, 0.31, 0.61)
            d_under = _sdf_segment(nx, ny, 0.51, 0.61, 0.69, 0.61)
            d_glyph = min(d_chev1, d_chev2, d_under)
            stroke_r = 0.042
            if d_glyph < stroke_r + aa:
                g_cov = max(0.0, min(1.0, 0.5 - (d_glyph - stroke_r) / (2.0 * aa)))
                r = int(r * (1.0 - g_cov) + 255 * g_cov)
                g = int(g * (1.0 - g_cov) + 255 * g_cov)
                b = int(b * (1.0 - g_cov) + 255 * g_cov)

            idx = (y * size + x) * 4
            buf[idx] = max(0, min(255, r))
            buf[idx + 1] = max(0, min(255, g))
            buf[idx + 2] = max(0, min(255, b))
            buf[idx + 3] = int(alpha_cov * 255)

    return bytes(buf)


def build_ico_bytes(sizes: tuple[int, ...] = (256, 64, 32)) -> bytes:
    """Construye los bytes completos de un archivo `.ico` con imágenes PNG RGBA."""
    png_frames = [_pack_png_rgba(s, s, _render_icon_rgba(s)) for s in sizes]
    count = len(sizes)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory = bytearray()
    payload = bytearray()

    for s, png in zip(sizes, png_frames, strict=True):
        w_byte = 0 if s >= 256 else s
        h_byte = 0 if s >= 256 else s
        entry = struct.pack(
            "<BBBBHHII",
            w_byte,
            h_byte,
            0,  # palette
            0,  # reserved
            1,  # color planes
            32,  # bits per pixel
            len(png),
            offset,
        )
        directory.extend(entry)
        payload.extend(png)
        offset += len(png)

    return header + bytes(directory) + bytes(payload)


def ensure_discoclaro_ico(target_path: Path) -> Path:
    """Escribe `target_path` (`discoclaro.ico`) si no existe o está vacío y
    devuelve su ruta."""
    if not target_path.is_file() or target_path.stat().st_size < 100:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(build_ico_bytes())
    return target_path
