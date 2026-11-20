from datetime import datetime, timezone

from app import config
from app.canonical import canonical, digest
from app.clock import instant, stamp
from app.errors import DomainError

from .rules import compare, conclusion, identity, template_change, verdict, version_at


def _versions(snapshot):
    return {row['line']: row for row in snapshot['templates']['versions']}


def _bindings(snapshot):
    return {row['label']: row['line'] for row in snapshot['templates']['bindings']}


def _prints(snapshot):
    return {entry['data']['label']: entry['data'] for entry in snapshot['intake']['accepted']}


def _candidates(snapshot):
    """补打候选：所有已作废面单，按作废时点与面单编号稳定排序。"""
    rows = [row for row in snapshot['tickets']['labels'] if row['state'] == 'revoked']
    return sorted(rows, key=lambda row: (instant(row['void']['at']), row['label']))


def _fingerprint(snapshot):
    """模板规则指纹：规则版本 + 模板来源内容。任一项变化，旧结论即失效。"""
    return digest(canonical({'rule_version': config.RULE_VERSION,
                             'templates': snapshot['manifest']['inputs']['templates']['sha256']}).encode())


def _preflight_id(run_id, fingerprint, at):
    """同一批次、同一模板规则、同一补打时点得到同一编号，用于幂等留档。"""
    return digest(canonical({'scope': 'cross-version-reprint', 'run_id': run_id,
                             'rules': fingerprint, 'at': at}).encode())


def _evaluate(snapshot, row, at, versions, bindings, prints, rule_version):
    label = row['label']
    original_version = versions.get(bindings.get(label))
    print_row = prints.get(label)
    fmt = print_row['format'] if print_row else None
    new_version = version_at(list(versions.values()), fmt, instant(at)) if fmt else None

    original = conclusion(print_row, original_version) if print_row else None
    new = conclusion(print_row, new_version) if print_row else None

    replaced = row.get('replaced_by')
    parcel_live = any(other['label'] != label and other['parcel'] == row['parcel'] and other['state'] == 'valid'
                      for other in snapshot['tickets']['labels'])
    kind, code, note = verdict(original, new, replaced, parcel_live)

    # 切换失败时保留原单与旧版结论，但结论只在本规则版本下有效；规则变化后须失效重算。
    retained = None
    if new is None:
        retained = {'label': label, 'rule_version': rule_version, 'version': identity(original_version),
                    'conclusion': original, 'void_at': row['void']['at'],
                    'note': '新版模板不可用，不改动原单，旧版版面结论继续有效'}

    return {
        'label': label, 'parcel': row['parcel'], 'original_at': row['at'],
        'void_at': row['void']['at'], 'void_reason': row['void']['reason'], 'replaced_by': replaced,
        'verdict': kind, 'code': code, 'basis_note': note,
        'original': {'version': identity(original_version), 'conclusion': original},
        'new': {'version': identity(new_version), 'conclusion': new},
        'difference': compare(original, new),
        'template_change': template_change(original_version, new_version),
        'retained': retained,
    }


def build(snapshot, at):
    rule_version = config.RULE_VERSION
    fingerprint = _fingerprint(snapshot)
    moment = stamp(instant(at))
    run_id = snapshot['manifest']['id']
    versions, bindings, prints = _versions(snapshot), _bindings(snapshot), _prints(snapshot)
    items = [_evaluate(snapshot, row, moment, versions, bindings, prints, rule_version)
             for row in _candidates(snapshot)]
    counts = {name: sum(1 for item in items if item['verdict'] == name) for name in ('direct', 'blocked', 'confirm')}
    return {
        'id': _preflight_id(run_id, fingerprint, moment),
        'run_id': run_id, 'rule_version': rule_version, 'rules': fingerprint, 'at': moment,
        'created': stamp(datetime.now(timezone.utc)),
        'basis': {'original': '按原单出单时点绑定模板', 'new': '按补打时点绑定模板'},
        'summary': dict(counts, total=len(items)),
        'items': items,
    }


def preflight(store, expected, at):
    if store.active() != expected:
        raise DomainError('STALE_RUN', 409, current=store.active())
    snapshot = store.find(expected)
    record = build(snapshot, at)
    saved = store.save_preflight(record)
    return {'id': saved['record']['id'], 'created': saved['created'], 'changed': saved['changed'],
            'data': saved['record']}


def listing(store, run_id):
    snapshot = store.find(run_id)
    fingerprint = _fingerprint(snapshot)
    records = store.preflights(run_id)
    current = [row for row in records if row.get('rules') == fingerprint]
    # 规则版本或模板内容变化后，旧记录不再作为有效结论，仅供追溯；须按当前规则重算才能重新生效。
    superseded = [{'id': row['id'], 'rule_version': row['rule_version'], 'created': row['created'],
                   'at': row['at'], 'summary': row['summary'], 'valid': False,
                   'reason': '结论按旧规则或旧模板生成，已失效，需按当前规则重算'}
                  for row in records if row.get('rules') != fingerprint]
    return {'run': snapshot['manifest']['id'], 'rule_version': config.RULE_VERSION,
            'records': current, 'superseded': superseded}

