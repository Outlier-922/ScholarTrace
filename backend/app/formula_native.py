"""Read-only PDF crop and native glyph positions; executed in a parse worker."""
import hashlib
import math
from pathlib import Path
import pymupdf


def check_pdf(path, expected_sha):
    with Path(path).open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != expected_sha:
        raise ValueError('pdf_hash_changed')


def render_region(path, expected_sha, page_number, region=None):
    check_pdf(path, expected_sha)
    with pymupdf.open(path) as pdf:
        page = pdf[page_number - 1]
        if page.rotation:
            raise ValueError('rotated_formula_page_not_supported')
        width, height = page.rect.width, page.rect.height
        area = page.rect if region is None else pymupdf.Rect(
            region[0] * width, region[1] * height, region[2] * width, region[3] * height
        )
        # Preserve detail in small crops while bounding oversized source pages.
        target = 1_400_000 if region is None else 2_000_000
        scale = min(1.5 if region is None else 3.0, math.sqrt(target / area.get_area()))
        if region is not None and min(area.width * scale, area.height * scale) < 12:
            raise ValueError('formula_crop_too_small')
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=area, alpha=False).tobytes('png')


def extract(path, expected_sha, page_number, boxes):
    check_pdf(path, expected_sha)
    with pymupdf.open(path) as pdf:
        page = pdf[page_number - 1]
        if page.rotation: raise ValueError('rotated_formula_page_not_supported')
        width, height = page.rect.width, page.rect.height
        rects = [pymupdf.Rect(b[0]*width,b[1]*height,b[2]*width,b[3]*height) for b in boxes]
        crop = pymupdf.Rect(min(r.x0 for r in rects),min(r.y0 for r in rects),max(r.x1 for r in rects),max(r.y1 for r in rects))
        crop = (crop + (-8,-8,8,8)) & page.rect
        glyphs = []
        for block in page.get_text('rawdict', clip=crop).get('blocks', []):
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    for char in span.get('chars', []):
                        b = char['bbox']; center = pymupdf.Point((b[0]+b[2])/2,(b[1]+b[3])/2)
                        if not any(center in r for r in rects): continue
                        glyphs.append({'char':char['c'],'bbox':[round(b[0]/width,6),round(b[1]/height,6),round(b[2]/width,6),round(b[3]/height,6)],'size':round(span['size'],2),'font':span['font'],'flags':span['flags']})
                        if len(glyphs)>800: raise ValueError('formula_glyph_limit')
        scale = min(2.0,math.sqrt(3_000_000/max(1,crop.width*crop.height)))
        png = page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=crop,alpha=False).tobytes('png')
        return {'glyphs':glyphs,'crop_bbox':[crop.x0/width,crop.y0/height,crop.x1/width,crop.y1/height],'image_sha256':hashlib.sha256(png).hexdigest()},png
