"""Pinned CSV rules inside the task budget; uploaded data is never code."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import plot_runtime, studio


class PlotSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    spec: studio.ChartSpec


def renderer_digest():
    return hashlib.sha256(Path(plot_runtime.__file__).read_text(encoding='utf-8').encode()).hexdigest()


def prepare(options):
    profile, _, parsed = studio.read_dataset(options.dataset_id)
    if profile['sha256'] != options.sha256:
        raise HTTPException(409, 'CSV与所选快照不一致，请重新选择数据')
    spec = options.spec.model_dump()
    if len(set(spec['y'])) != len(spec['y']):
        raise HTTPException(422, '指标列不可重复')
    try:
        prepared = plot_runtime.prepare_chart(parsed, spec)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {'dataset_id': profile['id'], 'title': profile['title'], 'sha256': profile['sha256'],
            'row_count': profile['row_count'], 'data_status': profile['data_status'],
            'spec': spec, 'renderer_sha256': renderer_digest(),
            'preview': {key: prepared[key] for key in ('used_records', 'dropped_rows', 'statistics', 'normalizations', 'warnings', 'caption')}}


def checked_files(result):
    folder = studio.repo.folder(result['id'])
    expected = result.get('files_sha256', {})
    names = studio.FILES - {'architecture.mmd'} | {'input.csv'}
    if set(expected) != names:
        raise ValueError('plot_files_missing')
    for name in sorted(names):
        path = folder / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
            raise ValueError('plot_artifact_changed')
    if expected['input.csv'] != result['sha256']:
        raise ValueError('plot_input_changed')
    return folder


def render(payload, step):
    frozen = payload['plot_spec']
    if frozen['renderer_sha256'] != renderer_digest():
        raise ValueError('plot_renderer_changed')
    profile, _, parsed = studio.read_dataset(frozen['dataset_id'])
    if profile['sha256'] != frozen['sha256'] or profile['data_status'] != frozen['data_status']:
        raise ValueError('plot_source_changed')
    prepared = plot_runtime.prepare_chart(parsed, frozen['spec'])
    manifest = studio.render_asset('chart', frozen['spec'], frozen['dataset_id'], artifact_id=step['artifact_id'])
    if any(manifest[key] != frozen[key] for key in ('dataset_id', 'sha256', 'data_status', 'spec')):
        raise ValueError('plot_artifact_binding_changed')
    if any(manifest[key] != prepared[key] for key in ('caption', 'used_records', 'dropped_rows', 'statistics', 'normalizations', 'warnings')):
        raise ValueError('plot_transformation_changed')
    # Re-read after rendering too. A concurrent modification cannot be committed
    # as a completed task, even if an unbound studio artifact was registered.
    current, _, _ = studio.read_dataset(frozen['dataset_id'])
    if current['sha256'] != frozen['sha256'] or current['data_status'] != frozen['data_status']:
        raise ValueError('plot_source_changed')
    folder = studio.repo.folder(manifest['id'])
    if json.loads((folder / 'manifest.json').read_text(encoding='utf-8')) != manifest:
        raise ValueError('plot_manifest_changed')
    hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
              for name in sorted(studio.FILES - {'architecture.mmd'} | {'input.csv'})}
    result = {**manifest, 'files_sha256': hashes, 'renderer_sha256': frozen['renderer_sha256'],
              'mode': 'controlled_csv_plot', 'scientific_validity_verified': False,
              'paper_results_inferred': False}
    checked_files(result)
    return result


async def execute(payload, step):
    return await run_in_threadpool(render, payload, step)


def markdown(result):
    return '\n\n'.join(['# CSV 科研图表', result['title'],
                        '数据状态：' + result['data_status'], '原始 CSV SHA-256：' + result['sha256'],
                        '## 图注', result['caption'], '## 明确规则',
                        json.dumps(result['spec'], ensure_ascii=False, indent=2),
                        '复现文件位于 plot/；数据真实性、独立性与研究结论未由绘图步骤验证。'])
