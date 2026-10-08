from __future__ import annotations

import hashlib
import io
import math
import statistics
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """保存发送给视觉模型的一张派生图片及其来源信息。"""

    source_name: str
    label: str
    width: int
    height: int
    sha256: str
    jpeg_bytes: bytes
    operations: tuple[str, ...] = ()


def _encode(image: Image.Image, quality: int) -> bytes:
    """将预处理图片编码成适合视觉模型传输的 JPEG。"""

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _enhance(image: Image.Image) -> Image.Image:
    """轻量增强页面对比度和小字号文字边缘。"""

    enhanced = ImageEnhance.Contrast(image).enhance(1.08)
    return enhanced.filter(ImageFilter.UnsharpMask(radius=1.0, percent=115, threshold=3))


def _fit(image: Image.Image, max_side: int) -> Image.Image:
    """在保持宽高比的前提下限制图片最长边。"""

    if max(image.size) <= max_side:
        return image
    resized = image.copy()
    resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return resized


def _upscale(
    image: Image.Image,
    *,
    min_readable_side: int,
    max_upscale_factor: float,
    max_side: int,
) -> tuple[Image.Image, float]:
    """在限定倍率和最大边长内放大低分辨率截图。"""

    short_side = min(image.size)
    if short_side >= min_readable_side:
        return image, 1.0
    factor = min(max_upscale_factor, min_readable_side / short_side, max_side / max(image.size))
    if factor <= 1.05:
        return image, 1.0
    size = tuple(max(1, round(value * factor)) for value in image.size)
    return image.resize(size, Image.Resampling.LANCZOS), factor


def _projection_variance(values: list[int]) -> float:
    """计算边缘投影的方差，供倾斜角度评分使用。"""

    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _alignment_score(edges: Image.Image, angle: float) -> float:
    """评价指定旋转角度下水平和垂直界面的对齐程度。"""

    rotated = edges.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=0)
    margin_x = max(1, rotated.width // 12)
    margin_y = max(1, rotated.height // 12)
    cropped = rotated.crop((margin_x, margin_y, rotated.width - margin_x, rotated.height - margin_y))
    rows = list(cropped.resize((1, cropped.height), Image.Resampling.BOX).getdata())
    columns = list(cropped.resize((cropped.width, 1), Image.Resampling.BOX).getdata())
    return _projection_variance(rows) + _projection_variance(columns)


def estimate_deskew_angle(image: Image.Image, max_angle: float = 15.0) -> float:
    """估算小角度倾斜截图需要应用的校正角度。"""

    if max_angle <= 0:
        return 0.0
    sample = image.convert("L")
    sample.thumbnail((700, 700), Image.Resampling.LANCZOS)
    if ImageStat.Stat(sample).var[0] < 4:
        return 0.0
    edges = ImageOps.autocontrast(sample.filter(ImageFilter.FIND_EDGES))
    limit = max(1, int(math.floor(max_angle)))
    coarse = [(float(angle), _alignment_score(edges, float(angle))) for angle in range(-limit, limit + 1)]
    baseline = next(score for angle, score in coarse if angle == 0)
    best_angle, best_score = max(coarse, key=lambda item: item[1])
    if abs(best_angle) < 0.75 or best_score <= baseline * 1.025:
        return 0.0
    refinements = [
        best_angle + offset / 4
        for offset in range(-3, 4)
        if -max_angle <= best_angle + offset / 4 <= max_angle
    ]
    refined_angle, refined_score = max(
        ((angle, _alignment_score(edges, angle)) for angle in refinements),
        key=lambda item: item[1],
    )
    return refined_angle if refined_score > baseline * 1.025 else 0.0


def _background_color(image: Image.Image) -> tuple[int, int, int]:
    """使用四角像素的中位数估算旋转补边背景色。"""

    sample_size = max(1, min(image.size) // 20)
    boxes = (
        (0, 0, sample_size, sample_size),
        (image.width - sample_size, 0, image.width, sample_size),
        (0, image.height - sample_size, sample_size, image.height),
        (image.width - sample_size, image.height - sample_size, image.width, image.height),
    )
    means = [ImageStat.Stat(image.crop(box)).mean for box in boxes]
    return tuple(round(statistics.median(values)) for values in zip(*means))


def _orientation_guide(image: Image.Image, cell_size: int = 560) -> Image.Image:
    """生成包含四个方向候选的参考图，帮助视觉模型判断页面方向。"""

    canvas = Image.new("RGB", (cell_size * 2, cell_size * 2), _background_color(image))
    draw = ImageDraw.Draw(canvas)
    for index, degrees in enumerate((0, 90, 180, 270)):
        candidate = image.rotate(degrees, expand=True, resample=Image.Resampling.BICUBIC)
        candidate.thumbnail((cell_size - 12, cell_size - 32), Image.Resampling.LANCZOS)
        column, row = index % 2, index // 2
        x = column * cell_size + (cell_size - candidate.width) // 2
        y = row * cell_size + 24 + (cell_size - 24 - candidate.height) // 2
        canvas.paste(candidate, (x, y))
        draw.rectangle(
            (column * cell_size, row * cell_size, (column + 1) * cell_size, row * cell_size + 22),
            fill=(32, 36, 43),
        )
        draw.text((column * cell_size + 8, row * cell_size + 5), f"rotation {degrees}", fill="white")
    return canvas


def _prepare_view(
    image: Image.Image,
    *,
    max_side: int,
    min_readable_side: int,
    max_upscale_factor: float,
) -> tuple[Image.Image, float]:
    """对单个全局图或切片执行放大、增强和尺寸限制。"""

    resized, factor = _upscale(
        image,
        min_readable_side=min_readable_side,
        max_upscale_factor=max_upscale_factor,
        max_side=max_side,
    )
    return _fit(_enhance(resized), max_side), factor


def _sample_tile_starts(starts: list[int], max_tiles: int) -> list[int]:
    """在保留首尾覆盖的前提下均匀限制超长截图切片数量。"""

    if len(starts) <= max_tiles:
        return starts
    if max_tiles <= 1:
        return [starts[0]]
    indices = {
        round(index * (len(starts) - 1) / (max_tiles - 1))
        for index in range(max_tiles)
    }
    return [starts[index] for index in sorted(indices)]


def prepare_image(
    raw: bytes,
    source_name: str,
    *,
    max_side: int = 2048,
    quality: int = 88,
    enable_tiles: bool = True,
    min_readable_side: int = 900,
    max_upscale_factor: float = 3.0,
    enable_auto_deskew: bool = True,
    max_deskew_angle: float = 15.0,
    enable_orientation_guide: bool = True,
    max_tiles: int = 8,
) -> list[PreparedImage]:
    """解析一张原始截图并生成全局图、方向图和必要的长图切片。"""

    if not raw:
        raise ValueError(f"图片 {source_name} 为空")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > 50_000_000:
                raise ValueError("图片像素数量超过 5000 万")
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise ValueError(f"无法解析图片 {source_name}: {exc}") from exc
    width, height = image.size
    if width < 64 or height < 64:
        raise ValueError(f"图片 {source_name} 尺寸过小（{width}x{height}）")

    common_operations: list[str] = ["exif_orientation", "rgb_conversion"]
    if enable_auto_deskew:
        correction_angle = estimate_deskew_angle(image, max_deskew_angle)
        if correction_angle:
            image = image.rotate(
                correction_angle,
                resample=Image.Resampling.BICUBIC,
                expand=True,
                fillcolor=_background_color(image),
            )
            common_operations.append(f"deskew:{correction_angle:+.2f}deg")
            width, height = image.size

    overview, overview_factor = _prepare_view(
        image,
        max_side=max_side,
        min_readable_side=min_readable_side,
        max_upscale_factor=max_upscale_factor,
    )
    overview_operations = list(common_operations)
    if overview_factor > 1:
        overview_operations.append(f"upscale:{overview_factor:.2f}x")
    overview_operations.extend(("contrast:1.08", "unsharp_mask"))
    outputs: list[tuple[str, Image.Image, tuple[str, ...]]] = [
        ("overview", overview, tuple(overview_operations))
    ]
    if enable_orientation_guide:
        guide = _orientation_guide(image)
        outputs.append(
            (
                "orientation-guide",
                _fit(guide, max_side),
                tuple(common_operations + ["orientation_candidates:0,90,180,270"]),
            )
        )

    long_ratio = max(width / height, height / width)
    if enable_tiles and long_ratio >= 2.2:
        vertical = height > width
        long_side = height if vertical else width
        short_side = width if vertical else height
        tile_long = min(long_side, max(short_side * 2, 900))
        stride = max(1, int(tile_long * 0.82))
        starts = list(range(0, max(1, long_side - tile_long + 1), stride))
        final_start = max(0, long_side - tile_long)
        if not starts or starts[-1] != final_start:
            starts.append(final_start)
        starts = _sample_tile_starts(starts, max(1, max_tiles))
        for index, start in enumerate(starts, 1):
            box = (
                (0, start, width, start + tile_long)
                if vertical
                else (start, 0, start + tile_long, height)
            )
            tile, tile_factor = _prepare_view(
                image.crop(box),
                max_side=max_side,
                min_readable_side=min_readable_side,
                max_upscale_factor=max_upscale_factor,
            )
            tile_operations = list(common_operations)
            if tile_factor > 1:
                tile_operations.append(f"upscale:{tile_factor:.2f}x")
            tile_operations.extend(("contrast:1.08", "unsharp_mask"))
            outputs.append((f"tile-{index}", tile, tuple(tile_operations)))

    result: list[PreparedImage] = []
    for label, item, operations in outputs:
        encoded = _encode(item, quality)
        result.append(
            PreparedImage(
                source_name=source_name,
                label=label,
                width=item.width,
                height=item.height,
                sha256=hashlib.sha256(encoded).hexdigest(),
                jpeg_bytes=encoded,
                operations=operations,
            )
        )
    return result


