"""Read common TeX fonts, fraction rules and script baselines from native PDFs."""

from bisect import bisect_right
import re


MATH_FONT = re.compile(r'^(?:CMR|CMMI|CMSY|CMEX|CMBX|MSBM)\d+')
SYMBOLS = {
    '∂': r'\partial ', '∥': r'\Vert ', '≤': r'\le ', '≥': r'\ge ',
    'α': r'\alpha ', 'β': r'\beta ', 'γ': r'\gamma ', 'θ': r'\theta ',
    'λ': r'\lambda ', 'μ': r'\mu ', 'σ': r'\sigma ', 'ϵ': r'\epsilon ',
    'ε': r'\varepsilon ', '−': '-', '×': r'\times ', '·': r'\cdot ',
    '∞': r'\infty ', '→': r'\to ',
}
OPERATORS = re.compile(r'(?<![A-Za-z])(ln|log|min|max|exp|sin|cos|tan)(?![A-Za-z])')


def _tex_run(run):
    text = run['text'].strip()
    font = run['font']
    if font.startswith('CMEX'):
        if text not in {'h', 'i'}:
            raise ValueError('unknown_large_delimiter')
        return '[' if text == 'h' else ']'
    if not MATH_FONT.match(font):
        if text == 'ReLU':
            return r'\operatorname{ReLU}'
        raise ValueError('ordinary_text_inside_formula')
    if font.startswith(('CMBX', 'MSBM')) and text.isalpha() and text.isascii():
        command = 'mathbf' if font.startswith('CMBX') else 'mathbb'
        return f'\\{command}{{{text}}}'
    if font.startswith('CMR'):
        text = OPERATORS.sub(lambda match: '\\' + match[1] + ' ', text)
        if re.search(r'[A-Za-z]', OPERATORS.sub('', run['text'])):
            raise ValueError('unknown_upright_text')
    elif font.startswith('CMSY'):
        text = re.sub(r'[A-Z]', lambda match: r'\mathcal{' + match[0] + '}', text)
    elif font.startswith('CMMI'):
        text = text.replace(' ', '')
    result = ''.join(SYMBOLS.get(c, c) for c in text)
    if re.search(r'[^\x20-\x7e]|[&#%$]', result):
        raise ValueError('unsupported_native_symbol')
    return result


def _script_line(base, runs, baseline, size):
    """Attach smaller runs to the preceding glyph by x and baseline."""
    if not base:
        raise ValueError('empty_math_line')
    base = sorted(base, key=lambda run:run['x'])
    positions = [run['x'] for run in base]
    scripts = {}
    for run in runs:
        index = bisect_right(positions, run['x'] + .1) - 1
        delta = baseline - run['baseline']
        if (index < 0 or run['size'] >= size * .88 or not .55 <= abs(delta) <= size * .63
                or run['x'] < base[index]['right'] - .65):
            raise ValueError('ambiguous_script')
        scripts.setdefault((index, '^' if delta > 0 else '_'), []).append(run)
    parts = []
    for index, run in enumerate(base):
        parts.append(run['latex'] if 'latex' in run else _tex_run(run))
        for suffix in ('^', '_'):
            parts_for_script = sorted(scripts.get((index, suffix), []), key=lambda item:item['x'])
            if parts_for_script:
                if max(p['baseline'] for p in parts_for_script) - min(p['baseline'] for p in parts_for_script) > .6:
                    raise ValueError('nested_or_mixed_scripts')
                parts.append(suffix + '{' + ''.join(_tex_run(part) for part in parts_for_script) + '}')
    return ''.join(parts)


def _fraction_line(runs):
    size = max(run['size'] for run in runs)
    base = [run for run in runs if abs(run['size'] - size) <= .3]
    baseline = base[0]['baseline']
    if any(abs(run['baseline'] - baseline) > .55 for run in base):
        raise ValueError('multiple_fraction_baselines')
    return _script_line(base, [run for run in runs if run not in base], baseline, size)


def _page_lines(layout):
    rules = layout.get('horizontal_rules', [])
    runs = [run for run in layout.get('runs', []) if run['text'].strip()]
    math_runs = [run for run in runs if MATH_FONT.match(run['font']) and not run['font'].startswith('CMEX')]
    if not math_runs or len(rules) > 12:
        raise ValueError('missing_native_math')
    size = max(run['size'] for run in math_runs)
    baselines = []
    for run in sorted(math_runs, key=lambda run:run['baseline']):
        if abs(run['size'] - size) <= .3 and (not baselines or run['baseline'] - baselines[-1] > .55):
            baselines.append(run['baseline'])
    if len(baselines) > 12:
        raise ValueError('too_many_baselines')
    lines = {baseline: [] for baseline in baselines}
    for left, y, right in rules:
        portions = [run for run in runs if left - .4 <= run['x'] < right
                    and run['right'] <= right + .65 and abs(run['baseline'] - y) <= size
                    and run['size'] < size * .88 and MATH_FONT.match(run['font'])]
        above = [run for run in portions if run['baseline'] < y]
        below = [run for run in portions if run['baseline'] > y]
        nearby = [baseline for baseline in baselines if 0 <= baseline - y <= size * .52]
        if not above or not below or len(nearby) != 1:
            raise ValueError('unresolved_horizontal_rule')
        numerator, denominator = _fraction_line(above), _fraction_line(below)
        lines[nearby[0]].append({'x':left, 'right':right, 'latex':r'\frac{' + numerator + '}{' + denominator + '}'})
        runs = [run for run in runs if run not in portions]
    scripts = {baseline: [] for baseline in baselines}
    last_math = max((run['x'] for run in math_runs if abs(run['baseline'] - baselines[-1]) <= .55), default=-1)
    for run in runs:
        baseline = min(baselines, key=lambda y:abs(y - run['baseline']))
        delta = baseline - run['baseline']
        if abs(delta) <= .55 and (MATH_FONT.match(run['font']) or run['text'].strip() == 'ReLU'):
            lines[baseline].append(run)
        elif run['font'].startswith('CMEX') and 0 < delta <= size * 1.3:
            lines[baseline].append(run)
        elif MATH_FONT.match(run['font']) and abs(delta) <= size * .63:
            scripts[baseline].append(run)
        elif (baseline == baselines[-1] and abs(delta) <= .55 and run['x'] >= last_math
              and re.fullmatch(r'\.|[A-Z][A-Za-z ]{3,}', run['text'].strip())):
            continue
        else:
            raise ValueError('unassigned_math_run')
    return [_script_line(lines[y], scripts[y], y, size) for y in baselines]


def recover(layouts):
    """Return one candidate for a connected, conservative subset of native layouts."""
    if not layouts or not any(layout.get('horizontal_rules') for layout in layouts):
        return None
    try:
        pages = [_page_lines(layout) for layout in layouts]
        if any(not page[-1].rstrip().endswith(('=', '+', '-', r'\le', r'\ge'))
               for page in pages[:-1]):
            return None
        latex = ' '.join(line.strip() for page in pages for line in page)
        expected = []
        for char in latex:
            if char in '([':
                expected.append(')' if char == '(' else ']')
            elif char in ')]' and (not expected or expected.pop() != char):
                return None
        return latex if not expected else None
    except (ValueError, KeyError):
        return None
