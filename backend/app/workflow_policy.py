"""Finite feedback policies; never expand sources, privileges, or paid retries."""
import copy
import hashlib
import json
import uuid

VERSION = 'bounded-feedback-v1'


class ModelStepFailure(Exception):
    def __init__(self, partial_result, cause):
        self.partial_result = partial_result
        self.error_code = type(cause).__name__
        super().__init__('model_step_failed')


def snapshot(payload, reason, detail=''):
    history = payload.setdefault('plan_history', [])
    version = len(history) + 1
    record = {'version': version, 'reason': reason, 'detail': detail,
              'source_digest': hashlib.sha256(json.dumps(payload['sources'], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'execution_model_disabled': payload.get('execution_model_disabled', False),
              'steps': [{key: copy.deepcopy(step[key]) for key in ('id', 'tool', 'depends_on', 'recovery_for', 'model_allowed') if key in step}
                        for step in payload['steps']]}
    history.append(record)
    payload['plan_version'] = version
    return record


def allowed(payload):
    return len(payload.get('plan_history', [])) - 1 < payload.get('max_replans', 0)


def recover_model_failure(payload, step_id, partial_result, error_code):
    if not payload.get('use_model') or payload.get('execution_model_disabled') or payload.get('recovery_policy') != 'local_fallback' or not allowed(payload):
        return False
    target = next(step for step in payload['steps'] if step['id'] == step_id)
    if target['tool'] != 'evidence' or target.get('recovery_for') or target.get('resolved_by'):
        return False
    recovery_id = 'local-recovery-' + step_id
    target.update(status='failed', error_code=error_code, partial_result=partial_result, resolved_by=recovery_id)
    recovery = {'id': recovery_id, 'tool': 'evidence', 'label': '保留原文并继续本地流程',
                'role': '反馈恢复', 'hint': '复用已检索原文；不重发模型请求，后续步骤使用本地工具',
                'depends_on': list(target['depends_on']), 'status': 'waiting', 'attempts': 0,
                'artifact_id': str(uuid.uuid5(uuid.UUID(payload['id']), recovery_id)),
                'result': None, 'recovery_for': step_id, 'model_allowed': False}
    position = payload['steps'].index(target) + 1
    payload['steps'].insert(position, recovery)
    for step in payload['steps'][position + 1:]:
        step['depends_on'] = [recovery_id if dependency == step_id else dependency for dependency in step['depends_on']]
    payload['execution_model_disabled'] = True
    payload['gaps'].append('模型步骤失败，原尝试保留；自动转本地候选流程，后续未再调用模型，研究答案仍待完成。')
    snapshot(payload, 'model_step_failed', '插入本地恢复步骤并重接依赖；未增加来源或请求权限。')
    return True


def disable_after_degradation(payload, reason):
    if payload.get('execution_model_disabled') or not payload.get('use_model'):
        return False
    if payload.get('recovery_policy') != 'local_fallback' or not allowed(payload):
        return False
    payload['execution_model_disabled'] = True
    payload['gaps'].append('观察到模型调用降级，后续仅执行本地工具；已保留的结果不覆盖。')
    snapshot(payload, reason, '关闭本任务后续模型调用，不自动重试或增加预算。')
    return True
