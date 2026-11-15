"""Read one bounded PNG from stdin and emit one local formula candidate."""
import json
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from rapid_latex_ocr import LaTeXOCR


def main():
    png = sys.stdin.buffer.read(8_000_001)
    if len(png) > 8_000_000:
        raise ValueError('formula_image_too_large')
    with Image.open(BytesIO(png), formats=['PNG']) as image:
        if image.width * image.height > 2_100_000:
            raise ValueError('formula_image_too_large')
        low, high = image.convert('L').getextrema()
        if high - low < 24:
            return print(json.dumps({'latex': ''}))
    root = Path(sys.argv[1])
    model = LaTeXOCR(
        image_resizer_path=root / 'image_resizer.onnx', encoder_path=root / 'encoder.onnx',
        decoder_path=root / 'decoder.onnx', tokenizer_json=root / 'tokenizer.json',
    )
    latex, _ = model(png)
    print(json.dumps({'latex': latex}, ensure_ascii=True))


if __name__ == '__main__':
    main()
