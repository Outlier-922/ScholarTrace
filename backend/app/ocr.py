"""Opt-in local RapidOCR adapter with an explicit, offline preparation step.

Health checks and parsing never download models. ``prepare_ocr`` copies the
official models shipped in the pinned RapidOCR wheel, verifies their hashes,
and loads all three ONNX sessions before marking the local runtime ready.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

MODEL_FILES = {
    "Det": "PP-OCRv6_det_small.onnx",
    "Cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "Rec": "PP-OCRv6_rec_small.onnx",
}
_ENGINE: Any = None
_ENGINE_KEY: tuple[str, int, int] | None = None


class OCRUnavailable(RuntimeError):
    """The optional local runtime or its prepared models are unavailable."""


def enabled() -> bool:
    return os.getenv("SCHOLARTRACE_ENABLE_OCR", "false").strip().lower() in {"1", "true", "yes"}


def model_dir() -> Path:
    configured = os.getenv("SCHOLARTRACE_OCR_MODEL_DIR")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2] / ".cache" / "ocr"


def _dependencies_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("rapidocr", "onnxruntime", "numpy"))


@lru_cache(maxsize=12)
def _sha256(path: str, size: int, modified_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_status() -> dict[str, Any]:
    supported = _dependencies_available()
    root = model_dir()
    status: dict[str, Any] = {"supported": supported, "enabled": enabled(), "ready": False,
                              "reason": "OCR依赖未安装", "model_dir": str(root)}
    if not supported:
        return status
    manifest = root / "ready.json"
    status["reason"] = "本地OCR模型尚未准备；请先显式运行prepare_ocr()"
    if not manifest.is_file():
        return status
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
        if record.get("python_version") != platform.python_version() or record.get("python_executable") != str(Path(sys.executable).resolve()):
            status["reason"] = "Python运行环境已变化，请在当前环境重新准备OCR"
            return status
        if record.get("rapidocr_version") != importlib.metadata.version("rapidocr"):
            status["reason"] = "OCR依赖版本已变化，请重新准备模型"
            return status
        if record.get("onnxruntime_version") != importlib.metadata.version("onnxruntime"):
            status["reason"] = "ONNX Runtime版本已变化，请重新准备模型"
            return status
        for filename in MODEL_FILES.values():
            path = root / filename
            metadata = path.stat()
            if _sha256(str(path), metadata.st_size, metadata.st_mtime_ns) != record["models"][filename]["sha256"]:
                status["reason"] = "OCR模型校验不通过，请重新准备模型"
                return status
    except (OSError, ValueError, KeyError, TypeError):
        status["reason"] = "OCR准备记录或模型不可用，请重新准备"
        return status
    status.update(ready=True, reason="本地OCR模型已准备并完成CPU会话加载验证")
    return status


def _build_engine(root: Path):
    from rapidocr import RapidOCR

    threads = max(1, min(4, int(os.getenv("SCHOLARTRACE_OCR_THREADS", "2"))))
    params: dict[str, Any] = {
        "Global.model_root_dir": str(root), "Global.log_level": "error",
        "Global.text_score": 0.35, "Global.max_side_len": 2200,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.use_cuda": False,
        "EngineConfig.onnxruntime.use_dml": False,
        "Det.limit_type": "max", "Det.limit_side_len": 1800,
    }
    for task, filename in MODEL_FILES.items():
        params[f"{task}.model_path"] = str(root / filename)
    return RapidOCR(params=params)


def prepare_ocr() -> dict[str, Any]:
    """Prepare packaged official weights locally; no document leaves the machine."""
    if not _dependencies_available():
        raise OCRUnavailable("请先安装rapidocr和onnxruntime")
    spec = importlib.util.find_spec("rapidocr")
    assert spec and spec.origin
    bundled = Path(spec.origin).parent / "models"
    root = model_dir()
    root.mkdir(parents=True, exist_ok=True)
    records = {}
    for filename in MODEL_FILES.values():
        source = bundled / filename
        destination = root / filename
        if not source.is_file():
            raise OCRUnavailable(f"已安装的RapidOCR包未提供所需官方模型：{filename}，请使用项目锁定版本")
        source_stat = source.stat()
        expected = _sha256(str(source), source_stat.st_size, source_stat.st_mtime_ns)
        destination_matches = False
        if destination.is_file():
            destination_stat = destination.stat()
            destination_matches = _sha256(str(destination), destination_stat.st_size, destination_stat.st_mtime_ns) == expected
        if not destination_matches:
            temporary = destination.with_suffix(".tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        records[filename] = {"sha256": expected, "size": source_stat.st_size,
                             "source": "official model included in installed RapidOCR wheel"}
    engine = _build_engine(root)
    # RapidOCR lazily initializes these sessions. Explicit preparation verifies
    # all of them now, rather than discovering missing/corrupt weights in a job.
    engine._load_det_model()
    engine._load_cls_model()
    engine._load_rec_model()
    record = {"python_version": platform.python_version(), "python_executable": str(Path(sys.executable).resolve()),
              "rapidocr_version": importlib.metadata.version("rapidocr"),
              "onnxruntime_version": importlib.metadata.version("onnxruntime"),
              "provider": "CPUExecutionProvider", "models": records}
    temporary = root / "ready.tmp"
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(root / "ready.json")
    global _ENGINE, _ENGINE_KEY
    _ENGINE, _ENGINE_KEY = engine, (str(root), os.getpid(), (root / "ready.json").stat().st_mtime_ns)
    return runtime_status()


def _engine():
    status = runtime_status()
    if not status["ready"]:
        raise OCRUnavailable(status["reason"])
    global _ENGINE, _ENGINE_KEY
    key = (str(model_dir()), os.getpid(), (model_dir() / "ready.json").stat().st_mtime_ns)
    if _ENGINE is None or _ENGINE_KEY != key:
        _ENGINE = _build_engine(model_dir())
        _ENGINE_KEY = key
    return _ENGINE


def extract_ocr(page) -> list[dict[str, Any]]:
    """Return OCR line blocks in unrotated crop coordinates (not pixel coords)."""
    import numpy as np
    import pymupdf as fitz

    scale = max(1.0, min(3.0, float(os.getenv("SCHOLARTRACE_OCR_SCALE", "2"))))
    # Cancel PDF rotation before rasterizing. Both OCR coordinates and native
    # get_text coordinates consequently describe the same unrotated CropBox.
    matrix = fitz.Matrix(scale, scale).prerotate(-page.rotation)
    pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
    output = _engine()(image)
    if output.boxes is None or output.txts is None or output.scores is None:
        return []
    width, height = float(page.cropbox.width), float(page.cropbox.height)
    lines = []
    for polygon, text, confidence in zip(output.boxes, output.txts, output.scores):
        text, confidence = str(text).strip(), float(confidence)
        if not text or not math.isfinite(confidence) or confidence < 0.35:
            continue
        points = [[min(width, max(0.0, float(x) / pixmap.width * width)),
                   min(height, max(0.0, float(y) / pixmap.height * height))] for x, y in polygon]
        x0, y0 = min(point[0] for point in points), min(point[1] for point in points)
        x1, y1 = max(point[0] for point in points), max(point[1] for point in points)
        if x1 <= x0 or y1 <= y0:
            continue
        lines.append({"text": text, "rect": [x0, y0, x1, y1], "font_size": (y1 - y0) * 0.75,
                      "body_size": (y1 - y0) * 0.75, "bold": False,
                      "confidence": round(min(1.0, max(0.0, confidence)), 6),
                      "polygon": points, "extraction": "rapidocr"})
    return lines


def source_line_spans(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Map recognized lines to exact offsets in the stored parse block."""
    metadata = block.get("metadata", {})
    if metadata.get("extraction") != "rapidocr":
        return []
    spans, cursor = [], 0
    for line in metadata.get("lines", []):
        text = line["text"]
        start = block["text"].find(text, cursor)
        if not text or start == -1:
            continue
        cursor = start + len(text)
        spans.append({"block_id": block["id"], "page": block["page"], "bbox": line["bbox"],
                      "start": start, "end": cursor, "text": text})
    return spans
