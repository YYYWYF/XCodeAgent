from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """为截图 UI 分析启用禁止额外字段的严格数据契约。"""

    model_config = ConfigDict(extra="forbid")


class ScreenshotBounds(StrictModel):
    """记录截图坐标系中的像素边界，供生成器还原真实几何比例。"""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ScreenshotLayoutRegion(StrictModel):
    """描述截图中一个可见区域的精确位置、职责和视觉样式。"""

    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    bounds: ScreenshotBounds
    layout: str
    visual_style: str
    typography: str
    visible_text: list[str] = Field(max_length=20)


class ScreenshotVisualObservation(StrictModel):
    """描述一张截图中可观察到的视觉事实。"""

    screenshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    suggested_rotation: Literal[0, 90, 180, 270] = 0
    source_viewport_width: int = Field(ge=64)
    source_viewport_height: int = Field(ge=64)
    page_content_bounds: ScreenshotBounds
    page_content_region: str
    app_shell: str
    layout: str
    dominant_background: str
    color_palette: list[str] = Field(max_length=12)
    typography: list[str] = Field(max_length=10)
    components: list[str] = Field(max_length=24)
    layout_regions: list[ScreenshotLayoutRegion] = Field(max_length=24)
    spacing_and_shape: str
    visual_summary: str


class ScreenshotPageMapping(StrictModel):
    """把一个 ProductPlan 页面映射到一组截图视觉来源。"""

    page_id: str = Field(min_length=1)
    screenshot_sha256s: list[str] = Field(min_length=1, max_length=10)
    primary_screenshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class ScreenshotUiAnalysis(StrictModel):
    """约束视觉模型返回的截图观察和页面映射结果。"""

    global_style_summary: str
    observations: list[ScreenshotVisualObservation]
    page_mappings: list[ScreenshotPageMapping]
    unresolved_questions: list[str] = Field(max_length=12)


class ScreenshotSingleImagePageDecision(StrictModel):
    """只让模型选择单张截图的页面，截图摘要由服务端绑定。"""

    page_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    global_style_summary: str
    unresolved_questions: list[str] = Field(max_length=12)


class ScreenshotVisualAudit(StrictModel):
    """描述视觉模型对生成代码与参考截图的一致性审查结果。"""

    overall_similarity: int = Field(ge=0, le=100)
    layout_similarity: int = Field(ge=0, le=100)
    component_similarity: int = Field(ge=0, le=100)
    color_similarity: int = Field(ge=0, le=100)
    typography_similarity: int = Field(ge=0, le=100)
    blocking_mismatches: list[str] = Field(max_length=12)
    repair_instruction: str
    explanation: str
