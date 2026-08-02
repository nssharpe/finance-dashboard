/* ==========================================================================
   Merchant rules
   --------------------------------------------------------------------------
   A rule matches a case-insensitive substring of a transaction's ORIGINAL
   label (raw_description) and renames / recategorizes every hit, past and
   future. The dialog prefills the whole raw label, so leaving it alone behaves
   like an exact match; shortening it broadens the rule.

   Depends on helpers defined in app.js: apiFetch, toast, fmtCurrency,
   ensureCategories, loadTransactions. Loaded after app.js.
   ========================================================================== */

const ruleModal       = document.getElementById('rule-modal');
const rulePattern     = document.getElementById('rule-pattern');
const ruleDescription = document.getElementById('rule-description');
const ruleCategory    = document.getElementById('rule-category');
const rulePreviewEl   = document.getElementById('rule-preview');
const ruleSaveBtn     = document.getElementById('rule-save');
const ruleCancelBtn   = document.getElementById('rule-cancel');
const rulesBody       = document.getElementById('rules-body');
const rulesSummary    = document.getElementById('rules-summary');

let rulePreviewTimer = null;
let ruleLastFocused  = null;

/** The lightning affordance shown on each transaction row. */
function buildRuleChip(txn) {
  const chip = document.createElement('span');
  chip.className = 'rule-chip';
  chip.textContent = '⚡';
  chip.title = 'Create a rule from this transaction';
  chip.setAttribute('role', 'button');
  chip.setAttribute('tabindex', '0');
  chip.setAttribute('aria-label', 'Create a merchant rule from this transaction');
  const open = e => { e.stopPropagation(); openRuleModal(txn); };
  chip.addEventListener('click', open);
  chip.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(e); }
  });
  return chip;
}

async function openRuleModal(txn) {
  ruleLastFocused = document.activeElement;

  const cats = await ensureCategories();
  ruleCategory.innerHTML = '<option value="">— leave category unchanged —</option>';
  for (const c of cats) {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    if (c === txn.category) opt.selected = true;
    ruleCategory.appendChild(opt);
  }

  // Match on the ORIGINAL label, not a display name a rule may have replaced.
  const raw = txn.raw_description || txn.description || '';
  rulePattern.value = raw;
  ruleDescription.value = (txn.description && txn.description !== raw) ? txn.description : '';

  rulePreviewEl.textContent = '';
  rulePreviewEl.classList.remove('warn');
  ruleModal.hidden = false;
  rulePattern.focus();
  rulePattern.select();
  refreshRulePreview();
}

function closeRuleModal() {
  ruleModal.hidden = true;
  clearTimeout(rulePreviewTimer);
  if (ruleLastFocused) ruleLastFocused.focus();
}

async function refreshRulePreview() {
  const pattern = rulePattern.value.trim();
  if (!pattern) {
    rulePreviewEl.textContent = 'Enter something to match on.';
    rulePreviewEl.classList.remove('warn');
    return;
  }
  try {
    const r = await apiFetch('/api/merchant-rules/preview', {
      method: 'POST',
      body: JSON.stringify({ pattern }),
    });
    const noun = r.count === 1 ? 'transaction' : 'transactions';
    rulePreviewEl.textContent = 'Matches ' + r.count + ' ' + noun + ' · ' + fmtCurrency(r.total_amount);
    // A far-too-broad pattern is the main foot-gun; surface it before saving.
    rulePreviewEl.classList.toggle('warn', r.count > 200);

    if (r.samples && r.samples.length) {
      const uniq = [];
      for (const s of r.samples) {
        if (!uniq.includes(s.raw_description)) uniq.push(s.raw_description);
      }
      const samples = document.createElement('div');
      samples.className = 'rule-preview-samples';
      samples.textContent = 'e.g. ' + uniq.slice(0, 3).join(' · ');
      rulePreviewEl.appendChild(samples);
    }
  } catch (err) {
    rulePreviewEl.textContent = 'Preview failed: ' + err.message;
  }
}

async function saveRule() {
  const pattern = rulePattern.value.trim();
  if (!pattern) { toast('Enter a pattern to match on.', 'error'); return; }

  const description = ruleDescription.value.trim();
  const category    = ruleCategory.value;
  if (!description && !category) {
    toast('A rule needs a new name, a category, or both.', 'error');
    return;
  }

  ruleSaveBtn.disabled = true;
  try {
    const r = await apiFetch('/api/merchant-rules', {
      method: 'POST',
      body: JSON.stringify({
        pattern: pattern,
        description: description || null,
        category: category || null,
      }),
    });
    const noun = r.applied === 1 ? 'transaction' : 'transactions';
    toast('Rule created — updated ' + r.applied + ' ' + noun + '.', 'success');
    closeRuleModal();
    loadTransactions();
    loadMerchantRules();
  } catch (err) {
    toast('Could not create rule: ' + err.message, 'error');
  } finally {
    ruleSaveBtn.disabled = false;
  }
}

async function loadMerchantRules() {
  if (!rulesBody) return;
  rulesBody.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  try {
    const data = await apiFetch('/api/merchant-rules');
    const rules = data.rules || [];
    rulesSummary.textContent = rules.length
      ? rules.length + ' rule' + (rules.length === 1 ? '' : 's')
      : '';

    if (!rules.length) {
      rulesBody.innerHTML =
        '<div class="empty-state">No rules yet. Click ⚡ on any transaction to add one.</div>';
      return;
    }

    const table = document.createElement('table');
    table.className = 'rules-table';
    table.innerHTML =
      '<thead><tr><th>Matches</th><th>Renames to</th><th>Category</th>' +
      '<th style="text-align:right">Transactions</th><th></th></tr></thead>';
    const tbody = document.createElement('tbody');

    for (const rule of rules) {
      const tr = document.createElement('tr');

      const pat = document.createElement('td');
      const code = document.createElement('span');
      code.className = 'rule-pattern';
      code.textContent = rule.pattern;
      pat.appendChild(code);
      tr.appendChild(pat);

      const name = document.createElement('td');
      name.textContent = rule.description || '—';
      tr.appendChild(name);

      const cat = document.createElement('td');
      cat.textContent = rule.category || '—';
      tr.appendChild(cat);

      const count = document.createElement('td');
      count.className = 'rule-count';
      count.style.textAlign = 'right';
      count.textContent = rule.match_count;
      tr.appendChild(count);

      const actions = document.createElement('td');
      actions.style.textAlign = 'right';
      const del = document.createElement('button');
      del.className = 'rule-delete';
      del.textContent = 'Delete';
      del.title = 'Delete this rule and restore the original labels';
      del.addEventListener('click', () => deleteRule(rule));
      actions.appendChild(del);
      tr.appendChild(actions);

      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    rulesBody.innerHTML = '';
    rulesBody.appendChild(table);
  } catch (err) {
    rulesBody.textContent = '';
    const box = document.createElement('div');
    box.className = 'empty-state';
    box.textContent = 'Could not load rules: ' + err.message;
    rulesBody.appendChild(box);
  }
}

async function deleteRule(rule) {
  const msg = 'Delete this rule?\n\nMatching: ' + rule.pattern + '\n\n' +
              rule.match_count + ' transaction(s) will revert to their original ' +
              'label and category.';
  if (!confirm(msg)) return;
  try {
    const r = await apiFetch('/api/merchant-rules/' + rule.id, { method: 'DELETE' });
    toast('Rule deleted — reverted ' + r.reverted + ' transaction(s).', 'success');
    loadMerchantRules();
    loadTransactions();
  } catch (err) {
    toast('Could not delete rule: ' + err.message, 'error');
  }
}

// ---- wiring ----
if (rulePattern) {
  rulePattern.addEventListener('input', () => {
    clearTimeout(rulePreviewTimer);
    rulePreviewTimer = setTimeout(refreshRulePreview, 250);
  });
  ruleSaveBtn.addEventListener('click', saveRule);
  ruleCancelBtn.addEventListener('click', closeRuleModal);
  ruleModal.addEventListener('click', e => { if (e.target === ruleModal) closeRuleModal(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !ruleModal.hidden) closeRuleModal();
  });
}
