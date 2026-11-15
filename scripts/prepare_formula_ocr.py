"""Prepare the optional offline formula OCR runtime in the project cache."""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import formula_ocr

RELEASE = 'https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/'
PACKAGES = [
    'rapid-latex-ocr==0.0.9', 'onnxruntime==1.22.1', 'numpy==1.26.4',
    'opencv-python==4.11.0.86', 'requests', 'tqdm',
]


def download_model(client, name, length):
    target = formula_ocr.MODEL_DIR / name
    expected = formula_ocr.SHA256[name]
    if target.is_file():
        stat = target.stat()
        if stat.st_size == length and formula_ocr.file_digest(target, length, stat.st_mtime_ns) == expected:
            return
    temporary = None
    try:
        print('准备 ' + name, flush=True)
        digest = hashlib.sha256()
        size = 0
        with client.stream('GET', RELEASE + name) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.part', delete=False) as stream:
                temporary = Path(stream.name)
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > length:
                        raise ValueError('模型大小与已知发布文件不符')
                    stream.write(chunk)
                    digest.update(chunk)
        if size != length or digest.hexdigest() != expected:
            raise ValueError('模型校验失败')
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    uv = shutil.which('uv')
    if not uv:
        raise SystemExit('请先安装 uv，再使用本项目的 Python 3.12 运行此脚本。')
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('请使用本项目的 Python 3.12。')
    if formula_ocr.runtime_identity():
        print('本地公式识别已准备。')
        return
    env = {**os.environ, 'UV_CACHE_DIR': str(formula_ocr.CACHE / 'uv')}
    if not formula_ocr.interpreter().is_file():
        subprocess.run([uv, 'venv', str(formula_ocr.ENV_DIR), '--python', sys.executable], env=env, check=True)
    subprocess.run([uv, 'pip', 'install', '--python', str(formula_ocr.interpreter()), *PACKAGES], env=env, check=True)
    formula_ocr.MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for name, length in formula_ocr.SIZES.items():
            download_model(client, name, length)
    subprocess.run([str(formula_ocr.interpreter()), '-I', '-c', 'from rapid_latex_ocr import LaTeXOCR'], check=True)
    (formula_ocr.MODEL_DIR / 'ready').write_text(formula_ocr.VERSION + '\n', encoding='utf8')
    print('本地公式识别已准备。')


if __name__ == '__main__':
    main()
