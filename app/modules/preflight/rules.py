from app.clock import instant
from app.errors import DomainError
from app.modules.coordinates.box import scalar
from app.modules.coordinates.calibration import transform
from app.modules.occlusion.rules import inspect

BLOCK_REASONS = {'ALREADY_REPLACED': '已由后继面单替换，再补打会产生第二张可发面单',
                 'PARCEL_ALREADY_LIVE': '同包裹已有可发面单，再补打会重复出单',
                 'NO_ISSUABLE_BASIS': '原版与新版都无法出版，换版不能消除问题'}


def version_at(versions, fmt, at):
    """补打时点命中的模板：同族取区间内最高修订号，左闭右开。"""
    eligible = [version for version in versions
                if version['format'] == fmt and instant(version['start']) <= at < instant(version['end'])]
    return max(eligible, key=lambda version: version['revision'], default=None)


def conclusion(row, version):
    """用某版模板的纸张与出版范围重算旧单版面。

    重打沿用旧单字段几何，但坐标要按目标模板纸面重新换算，所以对每版各算一次；
    模板缺失时无法得出结论，几何换算失败时记为不可出版。
    """
    if version is None:
        return None
    try:
        width, height = map(scalar, version['paper'])
        _, boxes = transform(row, width, height)
        issues, _ = inspect(boxes, version)
    except DomainError as error:
        return {'clean': False, 'issues': [{'code': error.code, 'fields': [], 'area_mm2': '0',
                                            'area_exact_mm2': '0', 'side': None}]}
    return {'clean': not issues, 'issues': issues}


def identity(version):
    if version is None:
        return None
    return {'line': version['line'], 'revision': version['revision'], 'format': version['format'],
            'quiet_mm': version['quiet_mm'], 'note': version['note'],
            'start': version['start'], 'end': version['end']}


def _key(issue):
    """问题的稳定身份：原因码、字段组合和静区侧，用于两版逐条对齐。"""
    return (issue['code'], tuple(issue['fields']), issue.get('side') or '')


def compare(original, new):
    """原版与新版逐条问题的增、减和面积变化，作为「差在哪里」的结构化依据。"""
    if original is None or new is None:
        return {'added': [], 'removed': [], 'changed': []}
    old_map = {_key(issue): issue for issue in original['issues']}
    new_map = {_key(issue): issue for issue in new['issues']}
    added = [issue for key, issue in new_map.items() if key not in old_map]
    removed = [issue for key, issue in old_map.items() if key not in new_map]
    changed = [{'code': old_map[key]['code'], 'fields': old_map[key]['fields'],
                'side': old_map[key].get('side'),
                'original_area_mm2': old_map[key]['area_mm2'], 'new_area_mm2': new_map[key]['area_mm2']}
               for key in old_map.keys() & new_map.keys()
               if old_map[key]['area_exact_mm2'] != new_map[key]['area_exact_mm2']]
    return {'added': sorted(added, key=_key), 'removed': sorted(removed, key=_key),
            'changed': sorted(changed, key=lambda row: (row['code'], tuple(row['fields']), row.get('side') or ''))}


def template_change(original_version, new_version):
    """两版模板本身的参数差异，即使版面结论相同也能说明换版改了什么。"""
    if original_version is None or new_version is None:
        return None
    fields = sorted(set(original_version['zones']) | set(new_version['zones']))
    zones = [field for field in fields
             if original_version['zones'].get(field) != new_version['zones'].get(field)]
    return {'revision': [original_version['revision'], new_version['revision']],
            'quiet_mm': [original_version['quiet_mm'], new_version['quiet_mm']],
            'zones_changed': zones,
            'paper': [original_version['paper'], new_version['paper']]}


def verdict(original, new, already_replaced, parcel_live):
    """三态裁决，返回状态、裁决码和一句话依据。"""
    if already_replaced:
        return 'blocked', 'ALREADY_REPLACED', BLOCK_REASONS['ALREADY_REPLACED']
    if parcel_live:
        return 'blocked', 'PARCEL_ALREADY_LIVE', BLOCK_REASONS['PARCEL_ALREADY_LIVE']
    if new is None:
        return 'confirm', 'TEMPLATE_SWITCH_FAILED', '补打时点无可用新版模板，保留原单与旧版结论'
    new_clean, original_clean = new['clean'], bool(original and original['clean'])
    if original_clean and new_clean:
        return 'direct', None, '原版与新版版面均无问题，采用任一版都可出单'
    if not new_clean and not original_clean:
        return 'blocked', 'NO_ISSUABLE_BASIS', BLOCK_REASONS['NO_ISSUABLE_BASIS']
    if not original_clean and new_clean:
        return 'confirm', 'CROSS_VERSION_DIVERGENCE', '原版有问题、新版干净，换版会把拦截单变成可发单'
    return 'confirm', 'CROSS_VERSION_DIVERGENCE', '原版干净、新版有问题，换版会把可发单变成拦截单'
