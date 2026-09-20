'use strict';
const $ = (id) => document.getElementById(id);
let csrf, constraints, current, editingRule = null, page = 1, loadVersion = 0;
const fields = [
  ['name', 'Scheme name', 'text'], ['government_level', 'Government level', ['CENTRAL', 'STATE']],
  ['state', 'State / UT', 'text'], ['ministry_or_department', 'Ministry / department', 'text'],
  ['category', 'Normalized category', 'text'], ['raw_category', 'Source category', 'text'],
  ['description', 'Description', 'textarea'],
  ['status_confidence', 'Confidence status', ['VERIFIED', 'LIKELY_ACTIVE', 'NEEDS_REVIEW']],
  ['official_url', 'Official source URL', 'url'], ['secondary_source_url', 'Secondary source URL', 'url'],
  ['last_checked_date', 'Last checked date', 'date'], ['benefit_type', 'Benefit type', 'text'],
  ['benefit_summary', 'Benefit summary', 'textarea'], ['benefit_amount', 'Benefit amount', 'text'],
  ['application_method', 'Application method', 'textarea'],
  ['official_application_url', 'Application URL', 'url'], ['confidence_notes', 'Confidence notes', 'textarea'],
  ['other_eligibility_conditions', 'Other eligibility conditions', 'textarea'],
];

function message(text = '', error = false) {
  $('message').textContent = text;
  $('message').classList.toggle('error', error);
}
async function api(path, options = {}) {
  const response = await fetch('/api/admin' + path, {...options,
    headers: {'Content-Type': 'application/json', 'X-Admin-CSRF': csrf || '', ...options.headers}});
  if (response.status === 401) { location.assign('/admin/login'); throw new Error('Session expired. Please sign in again.'); }
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((item) => `${item.loc.slice(1).join('.')}: ${item.msg}`).join('; ') : data.detail;
    throw new Error(detail || 'Request failed. Please try again.');
  }
  return data;
}
function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text ?? '—';
  return node;
}
function badge(value) {
  const node = element('span', value || 'Not recorded');
  node.className = 'badge';
  if (['VERIFIED', 'LIKELY_ACTIVE', 'NEEDS_REVIEW'].includes(value)) node.classList.add(value);
  return node;
}
function option(select, value, text = value) { select.add(new Option(text, value)); }
function button(text, action) {
  const node = element('button', text);
  node.type = 'button';
  node.addEventListener('click', () => run(action, node));
  return node;
}
async function run(action, control) {
  if (control) control.disabled = true;
  try { await action(); } catch (error) { message(error.message, true); }
  finally { if (control) control.disabled = false; }
}

async function list() {
  const version = ++loadVersion;
  $('catalogue').hidden = false;
  $('detail').hidden = true;
  const params = new URLSearchParams();
  for (const [key, value] of new FormData($('filters'))) if (value) params.set(key, value);
  params.set('page', page);
  const data = await api('/schemes?' + params);
  if (version !== loadVersion) return;
  $('scheme-rows').replaceChildren();
  for (const scheme of data.items) {
    const row = element('tr');
    const name = element('td');
    const link = element('a', scheme.name);
    link.href = '#scheme/' + scheme.id;
    name.append(link); row.append(name);
    for (const key of ['government_level', 'state', 'category']) row.append(element('td', scheme[key]));
    const confidence = element('td'); confidence.append(badge(scheme.status_confidence)); row.append(confidence);
    row.append(element('td', scheme.is_active ? 'Active' : 'Archived'));
    $('scheme-rows').append(row);
  }
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  $('count').textContent = data.total ? `${data.total} matching schemes` : 'No schemes match these filters.';
  $('page-label').textContent = `Page ${page} of ${pages}`;
  $('previous').disabled = page <= 1;
  $('next').disabled = page >= pages;
}

function renderMetadata() {
  $('metadata-fields').replaceChildren();
  for (const [key, title, type] of fields) {
    const label = element('label', title);
    const control = element(Array.isArray(type) ? 'select' : type === 'textarea' ? 'textarea' : 'input');
    control.name = key;
    if (Array.isArray(type)) {
      option(control, '', 'Not recorded');
      for (const value of type) option(control, value);
      if (current[key] && !type.includes(current[key])) option(control, current[key]);
    } else {
      if (type !== 'textarea') control.type = type;
      control.maxLength = ['state', 'category'].includes(key) ? 100 : key === 'name' ? 1000 : 20000;
    }
    control.value = current[key] ?? '';
    control.required = key === 'name';
    label.append(control); $('metadata-fields').append(label);
  }
}
function sourceLink(label, value) {
  if (!value) return;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error();
    const link = element('a', `${label}: ${value}`);
    link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
    $('source-links').append(link);
  } catch { $('source-links').append(element('p', `${label}: ${value} (invalid URL)`)); }
}
function clearRule() {
  editingRule = null;
  $('rule-form').reset();
  $('rule-heading').textContent = 'Add rule';
  updateType();
}
function updateType() {
  $('rule-type').textContent = `Value type: ${constraints.fields[$('rule-field').value]}. Existing server validation applies.`;
}
function editRule(rule) {
  editingRule = rule;
  $('rule-heading').textContent = 'Edit rule #' + rule.id;
  $('rule-field').value = rule.field;
  $('rule-operator').value = rule.operator;
  $('rule-value').value = JSON.stringify(rule.value);
  $('rule-confidence').value = rule.source_metadata?.rule_confidence ?? rule.source_metadata?.confidence ?? '';
  $('rule-notes').value = rule.source_metadata?.notes ?? '';
  updateType(); $('rule-field').focus();
}
function renderRules() {
  $('rule-rows').replaceChildren();
  for (const rule of current.rules) {
    const row = element('tr');
    for (const value of [rule.field, rule.operator, JSON.stringify(rule.value),
      rule.source_metadata?.rule_confidence ?? rule.source_metadata?.confidence, rule.source_metadata?.notes]) row.append(element('td', value));
    const actions = element('td');
    actions.append(button('Edit', () => editRule(rule)), button('Delete', async () => {
      if (!confirm(`Delete rule #${rule.id}: ${rule.field} ${rule.operator} ${JSON.stringify(rule.value)}? This changes eligibility.`)) return;
      await api(`/schemes/${current.id}/rules/${rule.id}`, {method: 'DELETE'});
      await detail(current.id); message('Rule deleted. Manual review conditions were preserved.');
    }));
    row.append(actions); $('rule-rows').append(row);
  }
  if (!current.rules.length) {
    const row = element('tr'), cell = element('td', 'No structured rules. This scheme still requires review.');
    cell.colSpan = 6; row.append(cell); $('rule-rows').append(row);
  }
}
async function detail(id) {
  const version = ++loadVersion;
  const scheme = await api('/schemes/' + id);
  if (version !== loadVersion) return;
  current = scheme;
  $('catalogue').hidden = true; $('detail').hidden = false;
  $('scheme-title').textContent = current.name;
  $('scheme-status').replaceChildren(element('span', current.is_active ? 'Active · ' : 'Archived · '), badge(current.status_confidence));
  $('toggle-active').textContent = current.is_active ? 'Deactivate scheme' : 'Activate scheme';
  $('source-links').replaceChildren();
  sourceLink('Official source', current.official_url);
  sourceLink('Secondary source', current.secondary_source_url);
  sourceLink('Application', current.official_application_url);
  renderMetadata(); renderRules(); clearRule();
  $('manual-conditions').replaceChildren();
  const conditions = [...new Set([...(current.manual_conditions || []), current.other_eligibility_conditions].filter(Boolean))];
  for (const condition of conditions) $('manual-conditions').append(element('li', condition));
  if (!conditions.length) $('manual-conditions').append(element('li', 'No additional manual conditions recorded.'));
}
async function navigate() {
  message();
  const match = location.hash.match(/^#scheme\/(\d+)$/);
  if (match) await detail(match[1]); else await list();
}

$('filters').addEventListener('submit', (event) => { event.preventDefault(); page = 1; run(list); });
$('filters').addEventListener('reset', () => { page = 1; setTimeout(() => run(list), 0); });
$('previous').addEventListener('click', () => { page--; run(list); });
$('next').addEventListener('click', () => { page++; run(list); });
$('metadata').addEventListener('submit', (event) => {
  event.preventDefault();
  run(async () => {
    const patch = {};
    for (const [key, value] of new FormData($('metadata'))) {
      if (value !== (current[key] ?? '')) patch[key] = value.trim() || null;
    }
    if (!Object.keys(patch).length) { message('No metadata changes to save.'); return; }
    await api('/schemes/' + current.id, {method: 'PATCH', body: JSON.stringify(patch)});
    await detail(current.id); message('Scheme metadata saved.');
  }, event.submitter);
});
$('toggle-active').addEventListener('click', () => run(async () => {
  const active = !current.is_active;
  if (!active && !confirm(`Deactivate “${current.name}”? It will be excluded from active scheme matching; its data and rules will be kept.`)) return;
  await api('/schemes/' + current.id, {method: 'PATCH', body: JSON.stringify({is_active: active})});
  await detail(current.id); message(active ? 'Scheme activated.' : 'Scheme archived. Data and rules preserved.');
}, $('toggle-active')));
$('rule-field').addEventListener('change', updateType);
$('cancel-rule').addEventListener('click', clearRule);
$('rule-form').addEventListener('submit', (event) => {
  event.preventDefault();
  run(async () => {
    let value;
    try { value = JSON.parse($('rule-value').value); } catch { throw new Error('Rule value must be valid JSON. See the examples below the form.'); }
    const field = $('rule-field').value;
    const payload = {definition: {field, operator: $('rule-operator').value, value,
      value_type: editingRule?.field === field ? editingRule.value_type : constraints.fields[field]},
      confidence: $('rule-confidence').value.trim() || null, notes: $('rule-notes').value.trim() || null};
    const path = `/schemes/${current.id}/rules` + (editingRule ? '/' + editingRule.id : '');
    await api(path, {method: editingRule ? 'PUT' : 'POST', body: JSON.stringify(payload)});
    await detail(current.id); message('Rule saved. Manual review conditions were preserved.');
  }, event.submitter);
});
$('logout').addEventListener('click', () => run(async () => {
  await api('/logout', {method: 'POST'}); location.assign('/admin/login');
}, $('logout')));
window.addEventListener('hashchange', () => run(navigate));
run(async () => {
  constraints = await api('/session'); csrf = constraints.csrf;
  for (const field of Object.keys(constraints.fields)) option($('rule-field'), field);
  for (const op of constraints.operators) option($('rule-operator'), op);
  const options = await api('/filters');
  for (const key of ['state_or_ut', 'category']) for (const value of options[key]) option($('filters').elements[key], value);
  await navigate();
});
