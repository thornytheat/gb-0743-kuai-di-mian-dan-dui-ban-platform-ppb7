import {$, clock, command, element, explanation, facts, field, fillSelection, summary, table} from './dom.js';

const states = {valid:'可发', revoked:'作废', isolated:'隔离'};
const verdicts = {direct:'可直接补打', blocked:'需要拦截', confirm:'等待确认'};
const verdictTone = {direct:'ok', blocked:'bad', confirm:'warn'};
const rejectRows = rows => rows.map(row => [row.line, row.code, explanation(row.code), row.raw]);
const rejectHeaders = ['源行','原因码','判定','原始记录'];

export function intake(data) {
  summary([['出单记录',data.input_rows,'行'],['采信行',data.accepted.length,'行'],['隔离行',data.excluded.length,'行','warn'],
    ['原因类别',Object.keys(data.reasons).length,'类'],['文件编码',data.encoding]]);
  if ($('intake-filter').value === 'accepted') {
    table('intake-table', ['源行','面单','包裹','出单时点','设备'], data.accepted.map(row =>
      [row.line,row.data.label,row.data.parcel,clock(row.data.at),row.data.device]));
  } else table('intake-table', rejectHeaders, rejectRows(data.excluded), '收件无隔离');
}

export function templates(data) {
  summary([['输入版本',data.input_rows,'条'],['有效版本',data.versions.length,'条'],['隔离版本',data.excluded.length,'条','warn'],
    ['成功绑定',data.bindings.filter(row => row.line !== null).length,'张'],['未绑定',data.bindings.filter(row => row.line === null).length,'张']]);
  table('template-table', ['源行','模板','修订','起点','终点','静区 mm','备注'], data.versions.map(row =>
    [row.line,row.format,row.revision,clock(row.start),clock(row.end),row.quiet_mm,row.note]));
  if ($('template-filter').value === 'excluded') table('template-bindings', rejectHeaders, rejectRows(data.excluded));
  else table('template-bindings', ['面单','修订','版本源行'], data.bindings.map(row => [row.label, row.revision, row.line]));
}

export function barcode(data) {
  const checks = data.flatMap(row => row.checks.map(item => ({...item, label:row.label, parcel:row.parcel, line:row.line})));
  const failed = checks.filter(row => row.code);
  summary([['已对读',data.length,'张'],['对读通过',data.filter(row => row.ok).length,'张'],['不符面单',data.filter(row => !row.ok).length,'张','warn'],
    ['编号不符',failed.filter(row => row.code === 'TRACKING_MISMATCH').length,'处'],['未知包裹',failed.filter(row => row.code === 'UNKNOWN_PARCEL').length,'件']]);
  const list = $('barcode-filter').value === 'failed' ? failed : checks;
  table('barcode-table',['源行','面单','包裹','字段','实读','台账','原因码'],list.map(row =>
    [row.line,row.label,row.parcel,field(row.field),row.seen,row.expected,row.code || '相符']),'无不符项');
  const groups = new Map();
  for (const row of failed) groups.set(row.code, (groups.get(row.code) || 0) + 1);
  facts('barcode-summary', [...groups].map(([code,count]) => [explanation(code),count,'warn']));
}

export function occlusion(data, openProof) {
  const issues = data.flatMap(row => row.issues.map(item => ({...item,label:row.label,line:row.version_line})));
  const count = code => issues.filter(row => row.code === code).length;
  summary([['已检版面',data.length,'张'],['通过',data.filter(row => row.ok).length,'张'],
    ['字段相交',count('FIELD_OVERLAP'),'处','warn'],['静区占用',count('QUIET_OCCUPIED'),'处','warn'],['出界',count('BOX_OUTSIDE_SHEET')+count('QUIET_EDGE'),'处']]);
  const filter = $('occlusion-filter').value;
  table('occlusion-table', ['面单','问题','原因码','字段','相交/越界 mm²','侧','模板源行'], issues.filter(row => filter === 'all' || row.code === filter).map(row =>
    [command(row.label, () => openProof(row.label)),explanation(row.code),row.code,row.fields.map(field).join(' / '),
      row.area_mm2 === '0' ? '＜0.000001' : row.area_mm2,field(row.side),row.line]),'当前筛选无热区问题');
  table('occlusion-passed', ['面单','静区宽度 mm','模板源行'],data.filter(row => row.ok).map(row => [command(row.label, () => openProof(row.label)),row.quiet_mm,row.version_line]));
}

export function reconcile(data) {
  summary([['出单总数',data.input_rows,'张'],['有效',data.valid,'张','ok'],['撤销',data.revoked,'张'],['隔离',data.isolated,'张','warn'],['勾稽差额',data.gap,'张']]);
  facts('balance-strip',[['包裹总数',data.parcels_total],['可发包裹',data.shippable,'ok'],['无可发单',data.unissued,'warn'],['多张可发',data.multiple_live.length],['勾稽',data.ok?'相符':'异常',data.ok?'ok':'bad']]);
  const filter = $('parcel-filter').value;
  table('parcel-table',['包裹','有效','撤销','隔离','可发面单'],data.parcels.filter(row => filter === 'all' || Boolean(row.valid) === (filter === 'ready')).map(row =>
    [row.parcel,row.valid,row.revoked,row.isolated,row.live_labels.join(' / ') || '—']),'没有匹配包裹');
}

export function ticketForm(data, active) {
  const isReprint = $('ticket-action').value === 'reprint';
  const choices = data.labels.filter(row => isReprint ? row.state === 'revoked' && !row.replaced_by : row.state === 'valid');
  const selected = fillSelection('ticket-target', choices.map(row => [row.label, `${row.label} · ${row.parcel}`]), $('ticket-target').value);
  $('ticket-target').disabled = !choices.length;
  $('ticket-new-field').hidden = !isReprint;
  $('ticket-new').required = isReprint;
  $('ticket-reason-field').hidden = isReprint;
  $('ticket-submit').textContent = isReprint ? '提交重打' : '登记作废';
  $('ticket-submit').disabled = !selected || !active;
  return choices.find(row => row.label === selected);
}

export function tickets(data, meta, openBatch) {
  summary([['留档面单',data.labels.length,'张'],['已替换',data.labels.filter(row => row.replaced_by).length,'张'],
    ['已作废',data.applied.length,'张'],['待重打',data.labels.filter(row => row.state === 'revoked' && !row.replaced_by).length,'张','warn'],['批次版本',meta.history.length,'个']]);
  const filter = $('ticket-filter').value;
  const rows = data.labels.filter(row => filter === 'all' || (filter === 'isolated' ? row.state === 'isolated' : row.replaces || row.void));
  table('tickets-table',['源行','面单','包裹','状态','旧单','后继','原因码'],rows.map(row =>
    [row.line,row.label,row.parcel,states[row.state],row.replaces,row.replaced_by,row.codes.join(' / ') || '—']));
  if ($('void-filter').value === 'applied') table('void-table',['源行','登记编号','面单','时点','原因'],data.applied.map(row =>
    [row.line,row.id,row.label,clock(row.at),explanation(row.reason)]),'没有已应用登记');
  else table('void-table',rejectHeaders,rejectRows(data.excluded_voids),'没有隔离登记');
  $('restore').hidden = meta.history.find(row => row.active)?.id === meta.manifest.id;
  table('history',['批次','生成时点','出单数','状态','动作'],meta.history.map(row =>
    [row.id.slice(0,12),clock(row.created),row.rows,row.active?'当前':'历史',
      row.id === meta.manifest.id ? '正在查看' : command('查看', () => openBatch(row.id))]));
}

const versionText = version => version ? `修订 ${version.revision} · 行 ${version.line}` : '无可用模板';
const issueText = issue => [explanation(issue.code), issue.fields.map(field).join('/'), issue.side ? field(issue.side) : '']
  .filter(Boolean).join(' · ');
const conclusionText = conclusion => !conclusion ? '无法判定' : conclusion.clean ? '无问题' : conclusion.issues.map(issueText).join('；');

export function preflight(data, state, patch) {
  const records = data.records;
  const record = records.find(row => row.id === state.record) || records[records.length - 1] || null;
  $('preflight-rule').textContent = `规则版本 ${data.rule_version}`;
  if (!record) {
    summary([['补打时点','—'],['可直接补打',0,'张','ok'],['需要拦截',0,'张','bad'],['等待确认',0,'张','warn'],['候选面单',0,'张']]);
    table('preflight-table',['面单','包裹','裁决','依据','原版结论','新版结论'], [], '尚未生成预判，请填写补打时点后生成');
    $('preflight-detail').replaceChildren(element('p','生成预判后在此查看原版与新版的逐张版面差异','empty'));
    table('preflight-history',['编号','规则版本','补打时点','裁决分布'], [], '没有预判记录');
    return;
  }
  const counts = record.summary;
  summary([['补打时点',clock(record.at)],['可直接补打',counts.direct,'张','ok'],['需要拦截',counts.blocked,'张','bad'],
    ['等待确认',counts.confirm,'张','warn'],['候选面单',counts.total,'张']]);
  table('preflight-table',['面单','包裹','裁决','依据','原版结论','新版结论'], record.items.map(item =>
    [command(item.label, () => patch({record: record.id, label: item.label})),item.parcel,
      element('span', verdicts[item.verdict], verdictTone[item.verdict]), item.basis_note,
      conclusionText(item.original.conclusion), conclusionText(item.new.conclusion)]));
  detail(record, state, patch);
  table('preflight-history',['编号','规则版本','补打时点','裁决分布'], [
    ...records.map(row => [command(row.id.slice(0,12), () => patch({record: row.id, label: null})),
      row.rule_version, clock(row.at), `${row.summary.direct} 直接 / ${row.summary.blocked} 拦截 / ${row.summary.confirm} 确认`]),
    ...data.superseded.map(row => [row.id.slice(0,12), row.rule_version, clock(row.at), '已失效 · 需重算']),
  ], '没有预判记录');
  if (data.superseded.length) {
    $('preflight-history').append(element('p', `旧规则版本下 ${data.superseded.length} 条结论已失效，须按当前规则重算`, 'empty warn'));
  }
}

function detail(record, state, patch) {
  const items = record.items;
  const chosen = items.find(item => item.label === state.label) || items[0];
  const target = $('preflight-detail');
  if (!chosen) { target.replaceChildren(element('p','没有已作废面单可补打','empty')); return; }
  const head = document.createElement('div');
  head.className = 'panel-head';
  head.append(element('h2', `${chosen.label} · ${verdicts[chosen.verdict]}`));
  const switcher = document.createElement('label');
  switcher.append(document.createTextNode('面单 '));
  const picker = document.createElement('select');
  for (const item of items) {
    const option = element('option', `${item.label} · ${verdicts[item.verdict]}`);
    option.value = item.label;
    if (item.label === chosen.label) option.selected = true;
    picker.append(option);
  }
  picker.addEventListener('change', () => patch({record: record.id, label: picker.value}));
  switcher.append(picker);
  head.append(switcher);
  const diff = chosen.difference;
  const changes = [
    ...diff.changed.map(item => `${issueText(item)}：${item.original_area_mm2} → ${item.new_area_mm2} mm²`),
    ...diff.added.map(item => `新增 ${issueText(item)}（${item.area_mm2} mm²）`),
    ...diff.removed.map(item => `消失 ${issueText(item)}（${item.area_mm2} mm²）`),
  ];
  const rows = [
    ['原版 · 出单时点', chosen.original.version
      ? `${versionText(chosen.original.version)} · ${conclusionText(chosen.original.conclusion)}` : '无可用模板'],
    ['新版 · 补打时点', chosen.new.version
      ? `${versionText(chosen.new.version)} · ${conclusionText(chosen.new.conclusion)}` : '无可用模板，切换失败，保留原单旧结论'],
    ['面积变化', changes.join('；') || '两版逐条问题一致'],
    ['模板改动', chosen.template_change
      ? `修订 ${chosen.template_change.revision.join('→')} · 静区 ${chosen.template_change.quiet_mm.join('→')} mm` +
        (chosen.template_change.zones_changed.length
          ? ` · 版位调整 ${chosen.template_change.zones_changed.map(field).join('/')}` : ' · 版位未变')
      : '无法对比'],
  ];
  if (chosen.retained && chosen.code === 'TEMPLATE_SWITCH_FAILED') {
    rows.push(['保留原单', `${chosen.retained.note}（作废于 ${clock(chosen.retained.void_at)}）`]);
  }
  const body = element('div', '', 'preflight-detail-body');
  for (const [name, value] of rows) {
    const line = element('div', '', 'fact');
    line.append(element('span', name, 'dim'), element('strong', value, 'mono'));
    body.append(line);
  }
  target.replaceChildren(head, body);
}

