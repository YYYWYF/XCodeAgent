from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from PIL import Image, ImageOps

from app.agents.screenshot_requirements.models import (
    MAX_SCREENSHOT_BYTES,
    SCREENSHOT_INPUT_ROOT,
    RequirementInput,
    ScreenshotReference,
)


_MIME_BY_FORMAT = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class UiReferenceImage:
    """保存一张供 UI 视觉还原使用的无损派生图。"""

    source_sha256: str
    source_name: str
    label: str
    width: int
    height: int
    mime_type: str
    content: bytes
    operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreparedUiScreenshot:
    """保存单张原截图及其映射图、全局图和细节切片。"""

    reference: ScreenshotReference
    mapping_overview: UiReferenceImage
    overview: UiReferenceImage
    details: tuple[UiReferenceImage, ...]


def detect_sidebar_width_ratio(
    image: UiReferenceImage,
    *,
    hint_x: int,
) -> float | None:
    """从多条水平扫描线寻找持续的侧栏背景分界，避免依赖模型估计像素宽度。"""

    try:
        with Image.open(io.BytesIO(image.content)) as opened:
            pixels = opened.convert("RGB")
    except (OSError, ValueError):
        return None
    width, height = pixels.size
    if width < 400 or height < 200:
        return None
    radius = max(3, round(width * 0.004))
    left = max(round(width * 0.08), hint_x - round(width * 0.10))
    right = min(round(width * 0.42), hint_x + round(width * 0.10))
    rows = [round(height * fraction) for fraction in (0.08, 0.15, 0.22, 0.30, 0.38, 0.46, 0.54, 0.62, 0.70, 0.78, 0.86, 0.93)]
    candidates: list[tuple[float, int, int]] = []
    for x in range(left, right):
        distances = []
        for y in rows:
            before = pixels.getpixel((x - radius, y))
            after = pixels.getpixel((x + radius, y))
            distances.append(sum(abs(a - b) for a, b in zip(before, after)) / 3)
        score = float(median(distances))
        coverage = sum(distance >= 3 for distance in distances)
        if score >= 3 and coverage >= len(rows) * 0.7:
            candidates.append((score, coverage, x))
    if not candidates:
        return None
    strongest = max(item[0] for item in candidates)
    positions = [x for score, _, x in candidates if score >= strongest * 0.9]
    return float(median(positions)) / width


def _resolve_input_path(workspace: str, relative_path: str) -> Path:
    """把截图路径限制在当前工作区专用截图目录，拒绝符号链接越界。"""

    root = Path(workspace).expanduser().resolve()
    input_root = (root / SCREENSHOT_INPUT_ROOT).resolve()
    try:
        input_root.relative_to(root)
    except ValueError as exc:
        raise ValueError("工作区截图输入目录不能指向工作区外部") from exc
    unresolved = root / relative_path
    if unresolved.is_symlink():
        raise ValueError(f"截图文件不能是符号链接：{relative_path}")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(input_root)
    except ValueError as exc:
        raise ValueError(f"截图路径超出允许目录：{relative_path}") from exc
    if not candidate.is_file():
        raise ValueError(f"截图文件不存在或不是普通文件：{relative_path}")
    return candidate


def _encode_png(image: Image.Image) -> bytes:
    """把图像无损编码为 PNG，避免需求识别用 JPEG 压缩改变 UI 色彩。"""

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, compress_level=6)
    return buffer.getvalue()


def _fit(image: Image.Image, max_side: int) -> tuple[Image.Image, bool]:
    """按比例限制最长边并返回是否发生过缩放。"""

    if max(image.size) <= max_side:
        return image.copy(), False
    resized = image.copy()
    resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return resized, True


def _reference_image(
    image: Image.Image,
    reference: ScreenshotReference,
    label: str,
    operations: tuple[str, ...],
) -> UiReferenceImage:
    """把 PIL 图片转换为带来源摘要的 UI 视觉输入对象。"""

    encoded = _encode_png(image)
    return UiReferenceImage(
        source_sha256=reference.sha256,
        source_name=reference.name,
        label=label,
        width=image.width,
        height=image.height,
        mime_type="image/png",
        content=encoded,
        operations=operations,
    )


def _tile_starts(long_side: int, tile_long: int, max_tiles: int) -> list[int]:
    """在覆盖首尾的前提下均匀选取有限数量的长截图切片起点。"""

    if long_side <= tile_long:
        return [0]
    stride = max(1, int(tile_long * 0.84))
    starts = list(range(0, max(1, long_side - tile_long + 1), stride))
    final_start = long_side - tile_long
    if not starts or starts[-1] != final_start:
        starts.append(final_start)
    if len(starts) <= max_tiles:
        return starts
    if max_tiles <= 1:
        return [starts[0]]
    indices = {
        round(index * (len(starts) - 1) / (max_tiles - 1))
        for index in range(max_tiles)
    }
    return [starts[index] for index in sorted(indices)]


def _prepare_single(
    raw: bytes,
    reference: ScreenshotReference,
    *,
    overview_max_side: int,
    mapping_max_side: int,
    tile_max_side: int,
    max_tiles: int,
) -> PreparedUiScreenshot:
    """对单张截图仅做方向纠正、必要缩放和无损切片，保留原始视觉。"""

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.width * opened.height > 50_000_000:
                raise ValueError("图片像素数量超过 5000 万")
            actual_mime = _MIME_BY_FORMAT.get(str(opened.format or "").upper())
            if actual_mime is None:
                raise ValueError(f"不支持的真实图片格式：{opened.format or 'unknown'}")
            if actual_mime != reference.mime_type:
                raise ValueError(
                    f"声明类型 {reference.mime_type} 与真实类型 {actual_mime} 不一致"
                )
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise ValueError(f"无法解析图片 {reference.name}: {exc}") from exc
    if image.width < 64 or image.height < 64:
        raise ValueError(
            f"图片 {reference.name} 尺寸过小（{image.width}x{image.height}）"
        )

    overview_image, overview_resized = _fit(image, overview_max_side)
    mapping_image, mapping_resized = _fit(image, mapping_max_side)
    base_operations = ("exif_orientation", "rgb_conversion", "lossless_png")
    overview_operations = base_operations + (
        ((f"fit:{overview_max_side}",) if overview_resized else ())
    )
    mapping_operations = base_operations + (
        ((f"fit:{mapping_max_side}",) if mapping_resized else ())
    )
    overview = _reference_image(
        overview_image,
        reference,
        "overview",
        overview_operations,
    )
    mapping_overview = _reference_image(
        mapping_image,
        reference,
        "mapping-overview",
        mapping_operations,
    )

    details: list[UiReferenceImage] = []
    long_ratio = max(image.width / image.height, image.height / image.width)
    if long_ratio >= 2.2 and max_tiles > 0:
        vertical = image.height > image.width
        long_side = image.height if vertical else image.width
        short_side = image.width if vertical else image.height
        tile_long = min(long_side, max(round(short_side * 1.65), 900))
        starts = _tile_starts(long_side, tile_long, max_tiles)
        for index, start in enumerate(starts, start=1):
            box = (
                (0, start, image.width, min(image.height, start + tile_long))
                if vertical
                else (start, 0, min(image.width, start + tile_long), image.height)
            )
            tile, resized = _fit(image.crop(box), tile_max_side)
            operations = base_operations + (f"crop:{box}",) + (
                ((f"fit:{tile_max_side}",) if resized else ())
            )
            details.append(
                _reference_image(
                    tile,
                    reference,
                    f"detail-{index}",
                    operations,
                )
            )
    return PreparedUiScreenshot(
        reference=reference,
        mapping_overview=mapping_overview,
        overview=overview,
        details=tuple(details),
    )


def load_ui_reference_images(
    workspace: str,
    requirement_input: RequirementInput,
    *,
    overview_max_side: int = 2048,
    mapping_max_side: int = 1280,
    tile_max_side: int = 2048,
    max_tiles: int = 4,
) -> list[PreparedUiScreenshot]:
    """校验截图清单的路径、大小、摘要和真实格式后生成保色视觉输入。"""

    prepared: list[PreparedUiScreenshot] = []
    for reference in requirement_input.screenshots:
        path = _resolve_input_path(workspace, reference.relative_path)
        raw = path.read_bytes()
        if len(raw) > MAX_SCREENSHOT_BYTES:
            raise ValueError(f"图片 {reference.name} 超过 15 MB")
        if len(raw) != reference.size:
            raise ValueError(f"图片 {reference.name} 的大小与上传清单不一致")
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise ValueError(f"图片 {reference.name} 的内容摘要与上传清单不一致")
        prepared.append(
            _prepare_single(
                raw,
                reference,
                overview_max_side=overview_max_side,
                mapping_max_side=mapping_max_side,
                tile_max_side=tile_max_side,
                max_tiles=max_tiles,
            )
        )
    if not prepared:
        raise ValueError("截图 UI 设计至少需要一张有效截图")
    return prepared


def mapping_images(
    screenshots: list[PreparedUiScreenshot],
    *,
    limit: int = 10,
) -> list[UiReferenceImage]:
    """为页面映射阶段每张原图只选择一个低分辨率全局视图。"""

    selected: list[UiReferenceImage] = []
    seen: set[str] = set()
    for item in screenshots:
        if item.reference.sha256 in seen:
            continue
        selected.append(item.mapping_overview)
        seen.add(item.reference.sha256)
        if len(selected) >= max(1, limit):
            break
    return selected


def page_images(
    screenshots: list[PreparedUiScreenshot],
    source_sha256s: list[str],
    *,
    limit: int,
) -> list[UiReferenceImage]:
    """按截图映射公平选择全局图和细节切片，限制单页视觉上下文。"""

    selected_hashes = {value for value in source_sha256s if value}
    selected: list[PreparedUiScreenshot] = []
    seen: set[str] = set()
    for item in screenshots:
        source = item.reference.sha256
        if source in selected_hashes and source not in seen:
            selected.append(item)
            seen.add(source)
    if not selected:
        return []
    safe_limit = max(1, limit)
    images: list[UiReferenceImage] = [item.overview for item in selected]
    detail_index = 0
    while len(images) < safe_limit:
        appended = False
        for item in selected:
            if detail_index < len(item.details):
                images.append(item.details[detail_index])
                appended = True
                if len(images) >= safe_limit:
                    break
        if not appended:
            break
        detail_index += 1
    return images[:safe_limit]
