from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from app.clock import instant
from app.modules.preflight import rules
from app.modules.preflight.service import build, preflight


def revoked(batch):
    return {row['label']: row for row in batch['tickets']['labels'] if row['state'] == 'revoked'}


def items(record):
    return {item['label']: item for item in record['items']}


# 基线：样例批次里两张作废面单，L-001 已有后继、L-015 尚无。

def test_only_revoked_labels_are_candidates(batch):
    record = build(batch, '2026-11-20T09:30:00+08:00')
    assert set(items(record)) == set(revoked(batch)) == {'L-001', 'L-015'}


def test_replaced_label_is_blocked_rather_than_reprinted(batch):
    item = items(build(batch, '2026-11-20T09:30:00+08:00'))['L-001']
    assert (item['verdict'], item['code'], item['replaced_by']) == ('blocked', 'ALREADY_REPLACED', 'L-001-R')


def test_clean_under_both_bindings_is_directly_reprintable(batch):
    item = items(build(batch, '2026-11-20T09:30:00+08:00'))['L-015']
    assert item['verdict'] == 'direct' and item['code'] is None
    assert item['original']['conclusion']['clean'] and item['new']['conclusion']['clean']


def test_new_side_is_resolved_at_reprint_moment_original_at_print_moment(batch):
    item = items(build(batch, '2026-11-20T09:30:00+08:00'))['L-015']
    assert item['original']['version']['revision'] == 1     # L-015 出单于 08:32，落在修订 1
    assert item['new']['version']['revision'] == 2          # 补打 09:30，落在修订 2
    assert item['template_change']['revision'] == [1, 2]


def test_new_side_falls_back_to_same_revision_outside_revision_two_window(batch):
    item = items(build(batch, '2026-11-20T10:05:00+08:00'))['L-015']
    assert item['new']['version']['revision'] == 1 and item['new']['version']['line'] == 1


# 换版造成结论翻转：同一张旧单在修订 1 干净，在修订 2 因路由版位上移而越出版位。

def flipped_batch(batch):
    """把 L-015 的路由码上移到 y=40：修订 1 版位 38–66 容纳，修订 2 版位上移到 44–66 便不再容纳。"""
    clone = deepcopy(batch)
    entry = next(item for item in clone['intake']['accepted'] if item['data']['label'] == 'L-015')
    entry['data']['fields']['route']['box'] = [12, 40, 30, 7]
    return clone


def test_same_old_label_flips_to_confirm_when_new_version_shrinks_zone(batch):
    clone = flipped_batch(batch)
    item = items(build(clone, '2026-11-20T09:30:00+08:00'))['L-015']
    assert item['original']['conclusion']['clean'] is True
    assert item['new']['conclusion']['clean'] is False
    assert item['verdict'] == 'confirm' and item['code'] == 'CROSS_VERSION_DIVERGENCE'
    assert [issue['code'] for issue in item['difference']['added']] == ['FIELD_OUTSIDE_ZONE']


def test_version_at_respects_half_open_boundaries(batch):
    versions = list(batch['templates']['versions'])
    assert rules.version_at(versions, 'CN100', instant('2026-11-20T09:00:00+08:00'))['revision'] == 2
    assert rules.version_at(versions, 'CN100', instant('2026-11-20T10:00:00+08:00'))['revision'] == 1
    assert rules.version_at(versions, 'CN200', instant('2026-11-20T09:30:00+08:00')) is None


def test_switch_failure_keeps_original_and_old_conclusion(batch):
    item = items(build(batch, '2026-11-20T10:30:00+08:00'))['L-015']
    assert item['verdict'] == 'confirm' and item['code'] == 'TEMPLATE_SWITCH_FAILED'
    assert item['new']['version'] is None
    assert item['original']['version']['revision'] == 1 and item['original']['conclusion']['clean']
    assert item['retained']['conclusion'] == item['original']['conclusion']


def test_original_clean_new_dirty_is_left_for_confirmation(batch):
    assert rules.verdict({'clean': True, 'issues': []}, {'clean': False, 'issues': []}, None, False)[0] == 'confirm'
    verdict, code, note = rules.verdict({'clean': False, 'issues': []}, {'clean': True, 'issues': []}, None, False)
    assert (verdict, code) == ('confirm', 'CROSS_VERSION_DIVERGENCE')


def test_neither_side_issuable_is_blocked(batch):
    verdict, code, _ = rules.verdict({'clean': False, 'issues': []}, {'clean': False, 'issues': []}, None, False)
    assert (verdict, code) == ('blocked', 'NO_ISSUABLE_BASIS')


# 接口与留档：重复执行、规则变化、切换失败。

def test_endpoint_archives_once_and_repeats_are_idempotent(client, batch_id):
    body = {'expected': batch_id, 'at': '2026-11-20T09:30:00+08:00'}
    first = client.post('/api/preflight', json=body).json
    second = client.post('/api/preflight', json=body).json
    assert first['created'] is True and second['created'] is False
    assert first['id'] == second['id']
    listing = client.get('/api/preflight').json['data']
    assert len(listing['records']) == 1 and listing['records'][0]['id'] == first['id']


def test_rerun_on_same_batch_does_not_add_preflight_records(client, batch_id):
    body = {'expected': batch_id, 'at': '2026-11-20T09:30:00+08:00'}
    client.post('/api/preflight', json=body)
    before = len(client.get('/api/preflight').json['data']['records'])
    client.post('/api/runs/rerun', json={'expected': batch_id})
    client.post('/api/preflight', json=body)
    after = len(client.get('/api/preflight').json['data']['records'])
    assert before == after == 1


def test_different_moments_are_separate_records(client, batch_id):
    for at in ('2026-11-20T09:10:00+08:00', '2026-11-20T09:30:00+08:00'):
        client.post('/api/preflight', json={'expected': batch_id, 'at': at})
    records = client.get('/api/preflight').json['data']['records']
    assert len({row['id'] for row in records}) == 2


def test_stale_expected_is_rejected_without_archiving(client, batch_id):
    client.post('/api/tickets/void', json={'expected': batch_id, 'label': 'L-002',
                                           'at': '2026-11-20T08:30:00+08:00', 'reason': 'LOST'})
    stale = client.post('/api/preflight', json={'expected': batch_id, 'at': '2026-11-20T09:30:00+08:00'})
    assert (stale.status_code, stale.json['code']) == (409, 'STALE_RUN')


def test_concurrent_repeat_archives_a_single_record(app, batch_id):
    body = {'expected': batch_id, 'at': '2026-11-20T09:30:00+08:00'}

    def submit(_):
        return app.test_client().post('/api/preflight', json=body).json

    with ThreadPoolExecutor(2) as pool:
        one, two = pool.map(submit, range(2))
    assert one['id'] == two['id'] and sorted([one['created'], two['created']]) == [False, True]
    assert len(app.test_client().get('/api/preflight').json['data']['records']) == 1


def test_rule_change_invalidates_old_records_and_recomputes(batch, tmp_path, monkeypatch):
    from app import config
    from app.store import Store
    from app.modules.tickets.service import seed
    store = Store(tmp_path / 'rule.sqlite3')
    seed(store)
    run_id = store.active()
    body_at = '2026-11-20T09:30:00+08:00'
    old = preflight(store, run_id, body_at)
    assert old['created'] and store.preflights(run_id)[0]['rule_version'] == '1.0.0'
    monkeypatch.setattr(config, 'RULE_VERSION', '1.1.0')
    from app.modules.preflight.service import listing
    view = listing(store, run_id)
    assert view['records'] == [] and len(view['superseded']) == 1 and view['superseded'][0]['valid'] is False
    refreshed = preflight(store, run_id, body_at)
    assert refreshed['created'] and refreshed['id'] != old['id']
    view = listing(store, run_id)
    assert view['records'][0]['rule_version'] == '1.1.0'


def test_rule_change_invalidates_same_moment_but_keeps_old_batch_intact(tmp_path):
    """换模板规则后，旧批次按原始输入独立复算；同一时点须在新批次下重算，不会沿用旧结论。"""
    from app.store import Store
    from app.modules.tickets.service import seed
    from app.modules.tickets.pipeline import run
    from app.modules.preflight.service import listing
    store = Store(tmp_path / 'switch.sqlite3')
    seed(store)
    old_run = store.active()
    at = '2026-11-20T09:30:00+08:00'
    old = preflight(store, old_run, at)
    sources = store.sources(old_run)
    sources['templates'] = sources['templates'].replace('早班版'.encode(), '早班版B'.encode())
    fresh = run(sources)
    store.save(fresh, sources, old_run)
    new_run = fresh['manifest']['id']
    assert new_run != old_run
    # 旧批次结论仍在旧批次下有效，但当前批次下没有同条件记录，需重算。
    assert [row['id'] for row in listing(store, old_run)['records']] == [old['id']]
    assert listing(store, new_run)['records'] == []
    refreshed = preflight(store, new_run, at)
    assert refreshed['id'] != old['id']
    assert listing(store, old_run)['records'][0]['rule_version'] == listing(store, new_run)['records'][0]['rule_version']
