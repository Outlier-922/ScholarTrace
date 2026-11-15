"""Run formula OCR locally in its own Python environment."""
import hashlib
import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.cache'
MODEL_DIR = CACHE / 'formula-ocr-models'
ENV_DIR = CACHE / 'formula-ocr-env'
RUNNER = ROOT / 'scripts' / 'run_formula_ocr.py'
VERSION = 'rapid-latex-ocr-0.0.9-v1'
SIZES = {'image_resizer.onnx': 38967751, 'encoder.onnx': 89008136,
         'decoder.onnx': 50952726, 'tokenizer.json': 24174}
SHA256 = {
    'image_resizer.onnx': 'e0b075c39700f64d50400f39c8fc186bbb3b5d84d31864008313f376603aca9d',
    'encoder.onnx': '01bf5dc25539ca0cd5b1bd29296ea495977a6ba5f629dc4178277809d26e5e7d',
    'decoder.onnx': 'bd695497bf1b22279b7626f5916c79226e1e244c84355f8da7edfd2d921d0072',
    'tokenizer.json': '1dc27b18d6a518d0d5ff3f4bb7bd98521fe80ad39e5b2a246d4109f1bb9d5019',
}


def interpreter():
    return ENV_DIR / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


@lru_cache(maxsize=12)
def file_digest(path, size, mtime):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_identity():
    if not interpreter().is_file():
        return None
    try:
        if (MODEL_DIR / 'ready').read_text(encoding='utf8').strip() != VERSION:
            return None
        for name, size in SIZES.items():
            path = MODEL_DIR / name
            stat = path.stat()
            if stat.st_size != size or file_digest(path, size, stat.st_mtime_ns) != SHA256[name]:
                return None
    except (OSError, ValueError):
        return None
    return VERSION + ':' + hashlib.sha256(json.dumps(SHA256, sort_keys=True).encode()).hexdigest()


def recognize(png):
    environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
                   if key in os.environ}
    result = subprocess.run(
        [str(interpreter()), '-I', str(RUNNER), str(MODEL_DIR)],
        input=png, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60, check=True,
        env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    return json.loads(result.stdout)['latex']
