"""Read-only PDF crop and native glyph positions; executed in a parse worker."""
import hashlib
import math
from collections import defaultdict
from pathlib import Path
import pymupdf


def check_pdf(path, expected_sha):
    with Path(path).open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != expected_sha:
        raise ValueError('pdf_hash_changed')


def _source_page(pdf, page_number):
    """Use the same unrotated CropBox space as the saved OCR and glyph coordinates."""
    page = pdf[page_number - 1]
    if page.rotation:
        # The document was opened for this operation and is never saved.
        page.set_rotation(0)
    return page


def render_region(path, expected_sha, page_number, region=None):
    check_pdf(path, expected_sha)
    with pymupdf.open(path) as pdf:
        page = _source_page(pdf, page_number)
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


def _split_neighboring_crops(page, scale, items, np):
    """Find a visible blank row between neighboring formulas in the same lane."""
    width, height = page.rect.width, page.rect.height
    for upper, lower in zip(items, items[1:]):
        a, b = upper["bbox"], lower["bbox"]
        if not -.004 <= b[1]-a[3] <= .010:
            continue
        middle = (a[3]+b[1])/2
        top = max(a[1]+.009, middle-.014)
        bottom = min(b[3]-.009, middle+.014)
        if bottom-top < .005:
            continue
        area = pymupdf.Rect(min(a[0], b[0])*width, top*height, max(a[2], b[2])*width, bottom*height)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=area, colorspace=pymupdf.csGRAY, alpha=False)
        ink = np.frombuffer(pixels.samples, dtype=np.uint8).reshape((pixels.height, pixels.width)) < 190
        occupied = np.flatnonzero(ink.any(axis=1))
        gaps = [(int(end-start), (pixels.y+(start+end)/2)/(height*scale))
                for start, end in zip(occupied, occupied[1:]) if end-start >= 3]
        if gaps:
            _, line = max(gaps, key=lambda pair: (pair[0], -abs(pair[1]-middle)))
            if a[1]+.009 <= line <= b[3]-.009:
                a[3] = b[1] = round(float(line), 6)


def refine_scan_regions(path, expected_sha, page_number, suggestions):
    """Use original pixels for neighboring cuts and trailing equation labels."""
    import numpy as np

    check_pdf(path, expected_sha)
    result = []
    with pymupdf.open(path) as pdf:
        page = _source_page(pdf, page_number)
        width, height = page.rect.width, page.rect.height
        scale = min(2.0, 1800 / max(width, height))
        grouped = defaultdict(list)
        candidates = [{**item, "bbox": list(item["bbox"])} for item in suggestions]
        for candidate in candidates:
            group = candidate.pop("split_group", None)
            if group is not None:
                grouped[group].append(candidate)
        for items in grouped.values():
            _split_neighboring_crops(page, scale, items, np)
        for candidate in candidates:
            direction = candidate.pop("label_trim", None)
            if direction is None:
                result.append(candidate)
                continue
            bounds = list(candidate["bbox"])
            clip = pymupdf.Rect(*(v * size for v, size in zip(bounds, (width, height) * 2)))
            pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, colorspace=pymupdf.csGRAY, alpha=False)
            ink = np.frombuffer(pixels.samples, dtype=np.uint8).reshape((pixels.height, pixels.width)) < 190
            axis = 0 if direction == "right" else 1
            occupied = np.flatnonzero(ink.any(axis=axis))
            total = (width if axis == 0 else height) * scale
            gap = .015 if axis == 0 else .0027
            maximum_label = .052 if axis == 0 else .024
            extent = pixels.width if axis == 0 else pixels.height
            at_edge = occupied.size and extent - occupied[-1] <= .024 * total
            splits = [i for i, distance in enumerate(np.diff(occupied))
                      if at_edge and distance >= gap * total
                      and occupied[-1] - occupied[i+1] <= maximum_label * total]
            if splits:
                separation = splits[-1]
                offset = pixels.x if axis == 0 else pixels.y
                endpoint = (offset + occupied[separation] + min(3, (occupied[separation+1] - occupied[separation])/2)) / total
                if bounds[axis] + (.028 if axis == 0 else .009) <= endpoint < bounds[axis+2]:
                    bounds[axis+2] = round(float(endpoint), 6)
                    candidate["bbox"] = bounds
            result.append(candidate)
    return result


def extract(path, expected_sha, page_number, boxes, padding=8):
    check_pdf(path, expected_sha)
    with pymupdf.open(path) as pdf:
        page = _source_page(pdf, page_number)
        width, height = page.rect.width, page.rect.height
        rects = [pymupdf.Rect(b[0]*width,b[1]*height,b[2]*width,b[3]*height) for b in boxes]
        crop = pymupdf.Rect(min(r.x0 for r in rects),min(r.y0 for r in rects),max(r.x1 for r in rects),max(r.y1 for r in rects))
        crop = (crop + (-padding, -padding, padding, padding)) & page.rect
        glyphs = []
        runs = []
        for block in page.get_text('rawdict', clip=crop).get('blocks', []):
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    letters = []
                    for char in span.get('chars', []):
                        b = char['bbox']; center = pymupdf.Point((b[0]+b[2])/2,(b[1]+b[3])/2)
                        if not any(center in r for r in rects): continue
                        glyphs.append({'char':char['c'],'bbox':[round(b[0]/width,6),round(b[1]/height,6),round(b[2]/width,6),round(b[3]/height,6)],'size':round(span['size'],2),'font':span['font'],'flags':span['flags']})
                        letters.append(char)
                        if len(glyphs)>800: raise ValueError('formula_glyph_limit')
                    if letters:
                        x, baseline = letters[0]['origin']
                        runs.append({'text': ''.join(c['c'] for c in letters), 'font': span['font'],
                                     'size': round(span['size'], 2), 'x': round(x - crop.x0, 2),
                                     'baseline': round(baseline - crop.y0, 2),
                                     'right': round(max(c['bbox'][2] for c in letters) - crop.x0, 2)})
        rules = []
        for drawing in page.get_drawings():
            for item in drawing['items']:
                if item[0] != 'l':
                    continue
                a, b = item[1:]
                left, right = sorted((a.x, b.x))
                if (abs(a.y - b.y) <= .35 and right - left >= 3 and crop.y0 <= a.y <= crop.y1
                        and crop.x0 <= left < right <= crop.x1):
                    rules.append([round(left - crop.x0, 2), round(a.y - crop.y0, 2), round(right - crop.x0, 2)])
        scale = min(2.0,math.sqrt(3_000_000/max(1,crop.width*crop.height)))
        png = page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),clip=crop,alpha=False).tobytes('png')
        return {'glyphs':glyphs,'native_layout':{'runs':runs, 'horizontal_rules':rules[:80]},
                'crop_bbox':[crop.x0/width,crop.y0/height,crop.x1/width,crop.y1/height],
                'image_sha256':hashlib.sha256(png).hexdigest()},png


def extract_continuation(path, expected_sha, page_number, region, continuation):
    parts = []
    for number, bounds in ((page_number, region), (page_number + 1, continuation)):
        try:
            value, png = extract(path, expected_sha, number, [bounds], padding=0)
            glyphs = [{**glyph, 'page': number} for glyph in value['glyphs']]
            layout = value['native_layout']
        except ValueError as error:
            if str(error) != 'formula_glyph_limit':
                raise
            png = render_region(path, expected_sha, number, bounds)
            glyphs = []
            layout = {}
        parts.append((glyphs, png, {'page':number, **layout}))
    return parts
