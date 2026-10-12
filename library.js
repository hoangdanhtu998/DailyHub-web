/* Thư viện AI: tài nguyên (repo/skill/toolkit/bài viết), Prompt Library, Memory Vault,
   nguồn & cài đặt, và dashboard đồng bộ. Mọi dữ liệu từ server được hiển thị bằng textContent. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const KIND = { repo: 'Repo', skill: 'Skill', toolkit: 'Toolkit', article: 'Bài viết' };
  const STATUS = { active: 'Đang hoạt động', archived: 'Đã lưu trữ', missing: 'Không còn truy cập được' };
  const MEM_KIND = { fact: 'Sự thật', preference: 'Sở thích', process: 'Quy trình', decision: 'Quyết định', lesson: 'Bài học', note: 'Ghi chú' };
  const SCORE_LABEL = { activity: 'Hoạt động', release: 'Release', adoption: 'Độ phổ biến (stars)', community: 'Cộng đồng (forks)', health: 'Tình trạng', docs: 'Mô tả/Docs', license: 'Giấy phép', status: 'Trạng thái' };
  const EVENT_LABEL = { new: 'Mới phát hiện', release: 'Release mới', missing: 'Ngừng truy cập', archived: 'Đã lưu trữ', recovered: 'Hoạt động lại' };

  /* ---------- helpers ---------- */
  const h = (tag, props, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'value') e.value = v;
      else if (k === 'checked') e.checked = !!v;
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : String(v));
    }
    kids.flat().forEach(c => { if (c == null || c === false) return; e.append(c instanceof Node ? c : document.createTextNode(String(c))); });
    return e;
  };
  const fmtDate = ms => (ms ? new Date(ms).toLocaleString('vi-VN') : 'chưa có');
  const fmtNum = n => (n == null ? '–' : Number(n).toLocaleString('vi-VN'));
  const safeHref = u => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
  const list = s => String(s || '').split(',').map(x => x.trim()).filter(Boolean);
  const toast = (node, msg, isError) => { node.replaceChildren(h('p', { class: isError ? 'hub-error' : 'hub-ok', role: 'status', text: msg })); };

  async function api(method, path, body, timeoutMs = 0) {
    const opts = { method, headers: { Accept: 'application/json' } };
    if (timeoutMs > 0) opts.signal = AbortSignal.timeout(timeoutMs);
    if (method !== 'GET') opts.headers['X-DailyHub'] = '1';
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch('/api/hub' + path, opts); }
    catch { throw new Error('Không kết nối được máy chủ. Hãy chạy Run-FreeLLMAPI-Spider.bat và mở trang qua http://127.0.0.1:8766.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Lỗi HTTP ${res.status}`);
    return data;
  }
  const download = (name, text) => {
    const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    const a = h('a', { href: url, download: name }); document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  /* ---------- state ---------- */
  const state = { tab: 'search', searchQ: '', res: { kind: '', q: '', sort: 'score', saved: false, unseen: false, author: '', offset: 0 },
    editingPrompt: null, editingMemory: null, aiResourceRanks: new Map(), memFilter: { q: '', kind: '', stale: false, pinned: false } };
  let aiRankError = '';
  let aiRankRetryAfter = 0;
  const body = () => $('lib-body');

  async function rankResourceItems(items) {
    const missing = items.filter(item => !state.aiResourceRanks.has(item.id));
    if (missing.length && Date.now() >= aiRankRetryAfter) {
      try {
        for (let i = 0; i < missing.length; i += 40) {
          const ranked = await api('POST', '/ai/rank-resources', { items: missing.slice(i, i + 40) });
          for (const item of ranked.items || []) state.aiResourceRanks.set(item.id, item);
        }
        aiRankError = '';
      } catch (err) {
        aiRankError = err.message;
        aiRankRetryAfter = Date.now() + 30000;
      }
    }
    return items.map(item => ({ ...item, aiRank: state.aiResourceRanks.get(item.id) }));
  }

  /* ---------- Tài nguyên ---------- */
  function resourceCard(r) {
    const href = safeHref(r.url);
    const m = r.metrics || {};
    const saveBtn = h('button', { type: 'button', class: 'hub-btn', 'aria-pressed': String(r.saved), text: r.saved ? '★ Đã lưu' : '☆ Lưu',
      onclick: async () => { await api('PATCH', `/resources/${r.id}/state`, { saved: !r.saved }); renderResources(); } });
    const seenBtn = h('button', { type: 'button', class: 'hub-btn', text: r.seen ? 'Đã xem ✓' : 'Đánh dấu đã xem',
      onclick: async () => { await api('PATCH', `/resources/${r.id}/state`, { seen: !r.seen }); renderResources(); } });
    const rating = h('select', { class: 'hub-input hub-small', 'aria-label': 'Đánh giá',
      onchange: async e => { await api('PATCH', `/resources/${r.id}/state`, { rating: e.target.value ? Number(e.target.value) : null }); } },
      h('option', { value: '', text: 'Chưa đánh giá' }),
      [1, 2, 3, 4, 5].map(n => h('option', { value: n, text: '★'.repeat(n), selected: r.rating === n })));
    const note = h('textarea', { class: 'hub-input', rows: 2, placeholder: 'Ghi chú cá nhân…', 'aria-label': 'Ghi chú cá nhân', text: r.note,
      onchange: async e => { await api('PATCH', `/resources/${r.id}/state`, { note: e.target.value }); } });
    const breakdown = Object.entries(r.score_breakdown || {}).map(([k, v]) =>
      h('li', { text: `${SCORE_LABEL[k] || k}: ${v.points == null ? '–' : v.points}${v.max ? '/' + v.max : ''} — ${v.reason || ''}` }));
    const aiRank = r.aiRank || state.aiResourceRanks.get(r.id);
    return h('article', { class: 'hub-card' + (r.seen ? ' is-seen' : '') },
      h('div', { class: 'hub-row' },
        h('span', { class: 'hub-badge kind-' + r.kind, text: KIND[r.kind] || r.kind }),
        r.status !== 'active' ? h('span', { class: 'hub-badge warn', text: STATUS[r.status] || r.status }) : null,
        h('span', { class: 'hub-score', text: aiRank ? `Điểm AI ${aiRank.score}/100`
          : r.score == null ? 'AI chưa chấm điểm' : `Điểm kỹ thuật ${r.score}/100` })),
      h('h3', { class: 'hub-title' }, href ? h('a', { href, target: '_blank', rel: 'noopener noreferrer', text: r.name }) : r.name),
      r.author ? h('p', { class: 'ov-muted', text: 'Tác giả/tổ chức: ' + r.author }) : null,
      r.description ? h('p', { class: 'hub-desc', text: r.description }) : null,
      r.topics && r.topics.length ? h('p', { class: 'hub-tags', text: r.topics.map(t => '#' + t).join(' ') }) : null,
      h('p', { class: 'ov-muted', text: [
        m.stars != null ? `★ ${fmtNum(m.stars)} · forks ${fmtNum(m.forks)} · issues ${fmtNum(m.open_issues)}` : null,
        m.latest_release ? `Release mới nhất: ${m.latest_release.tag}` : (r.kind === 'article' ? null : 'Release: chưa xác minh'),
        r.kind === 'article' ? null : (r.metrics_at ? `Số liệu lấy lúc ${fmtDate(r.metrics_at)}` : 'Số liệu: chưa xác minh'),
        r.last_updated ? `Cập nhật nguồn: ${r.last_updated}` : null,
        r.license ? `Giấy phép: ${r.license}` : null,
      ].filter(Boolean).join(' · ') }),
      aiRank ? h('details', { class: 'hub-details' }, h('summary', { text: 'Vì sao có điểm AI này?' }), h('p', { class: 'ov-muted', text: aiRank.reason }))
        : breakdown.length ? h('details', { class: 'hub-details' }, h('summary', { text: 'Tiêu chí điểm kỹ thuật' }), h('ul', {}, breakdown)) : null,
      h('div', { class: 'hub-row' }, saveBtn, seenBtn, rating),
      note);
  }

  function resourceQuery(r) {
    const params = new URLSearchParams({ sort: r.sort, limit: 20, offset: r.offset });
    if (r.kind) params.set('kind', r.kind);
    if (r.q) params.set('q', r.q);
    if (r.author) params.set('author', r.author);
    if (r.saved) params.set('saved', '1');
    if (r.unseen) params.set('unseen', '1');
    return params;
  }

  async function renderResources() {
    const r = state.res;
    r.offset = 0;
    const data = await api('GET', '/resources?' + resourceQuery(r).toString());
    data.items = await rankResourceItems(data.items);
    if (r.sort === 'score') data.items.sort((a, b) => (b.aiRank?.score ?? b.score ?? 0) - (a.aiRank?.score ?? a.score ?? 0));
    const select = (id, label, opts, value, onchange) => h('label', { class: 'hub-field' }, label,
      h('select', { id, class: 'hub-input', onchange }, opts.map(([v, t]) => h('option', { value: v, text: t, selected: v === value }))));
    const filters = h('div', { class: 'hub-filters' },
      select('f-kind', 'Loại', [['', 'Tất cả'], ['repo', 'Repo'], ['skill', 'Skill'], ['toolkit', 'Toolkit'], ['article', 'Bài viết']], r.kind,
        e => { r.kind = e.target.value; r.offset = 0; renderResources(); }),
      select('f-sort', 'Sắp xếp', [['score', 'Điểm chất lượng'], ['updated', 'Cập nhật gần nhất'], ['stars', 'Stars'], ['new', 'Mới phát hiện'], ['name', 'Tên']], r.sort,
        e => { r.sort = e.target.value; r.offset = 0; renderResources(); }),
      select('f-author', 'Tác giả/tổ chức', [['', 'Tất cả'], ...data.authors.map(a => [a, a])], r.author,
        e => { r.author = e.target.value; r.offset = 0; renderResources(); }),
      h('label', { class: 'hub-check' }, h('input', { type: 'checkbox', checked: r.saved, onchange: e => { r.saved = e.target.checked; r.offset = 0; renderResources(); } }), ' Chỉ đã lưu'),
      h('label', { class: 'hub-check' }, h('input', { type: 'checkbox', checked: r.unseen, onchange: e => { r.unseen = e.target.checked; r.offset = 0; renderResources(); } }), ' Chưa xem'));
    const search = h('form', { class: 'hub-row', onsubmit: e => { e.preventDefault(); r.q = e.target.q.value.trim(); r.offset = 0; renderResources(); } },
      h('input', { name: 'q', class: 'hub-input', type: 'search', maxlength: 100, placeholder: 'Tìm tên, mô tả, tác giả, topic…', value: r.q, 'aria-label': 'Tìm tài nguyên' }),
      h('button', { class: 'hub-btn', type: 'submit', text: 'Tìm' }));
    const items = data.items.map(resourceCard);
    const listEl = h('div', { class: 'hub-list' }, items.length ? items : h('p', { class: 'ov-muted', text: data.total === 0 && !r.q && !r.kind ? 'Chưa có tài nguyên. Hệ thống sẽ điền sau lần đồng bộ đầu tiên (xem tab Nguồn & cài đặt), hoặc bấm “Chạy ngay” ở Tổng quan.' : 'Không có tài nguyên phù hợp bộ lọc.' }));
    const more = data.total > data.items.length
      ? h('button', { class: 'hub-btn', type: 'button', text: 'Xem thêm', onclick: async () => {
          r.offset += data.items.length;
          const next = await api('GET', '/resources?' + resourceQuery(r).toString());
          next.items = await rankResourceItems(next.items);
          if (r.sort === 'score') next.items.sort((a, b) => (b.aiRank?.score ?? b.score ?? 0) - (a.aiRank?.score ?? a.score ?? 0));
          listEl.append(...next.items.map(resourceCard));
          data.items = data.items.concat(next.items);
          if (data.total <= r.offset) more.remove();
        } }) : null;
    body().replaceChildren(h('p', { class: 'ov-muted', text: `${fmtNum(data.total)} tài nguyên khớp bộ lọc. AI xếp hạng theo mô tả và dữ liệu nguồn; khi AI mất kết nối, danh sách vẫn hiện điểm kỹ thuật.` }),
      aiRankError ? h('p', { class: 'hub-warn', text: `AI chưa xếp hạng được: ${aiRankError}` }) : null, filters, search, listEl, more);
  }

  /* ---------- Prompt Library ---------- */
  function promptForm(p) {
    const f = {
      title: h('input', { class: 'hub-input', maxlength: 120, value: p?.title || '', 'aria-label': 'Tiêu đề prompt' }),
      goal: h('input', { class: 'hub-input', maxlength: 300, value: p?.goal || '', placeholder: 'Mục tiêu, ví dụ: tóm tắt bài báo', 'aria-label': 'Mục tiêu' }),
      body: h('textarea', { class: 'hub-input', rows: 8, maxlength: 20000, 'aria-label': 'Nội dung prompt', text: p?.body || '' }),
      variables: h('input', { class: 'hub-input', value: (p?.variables || []).join(', '), placeholder: 'Biến đầu vào, ví dụ: text, language', 'aria-label': 'Biến đầu vào' }),
      models: h('input', { class: 'hub-input', value: (p?.models || []).join(', '), placeholder: 'Model tương thích, ví dụ: claude-sonnet, gpt-4o', 'aria-label': 'Model tương thích' }),
      tags: h('input', { class: 'hub-input', value: (p?.tags || []).join(', '), placeholder: 'Tag, ví dụ: viết, dịch', 'aria-label': 'Tag' }),
      result_note: h('textarea', { class: 'hub-input', rows: 2, maxlength: 2000, 'aria-label': 'Nhận xét kết quả', text: p?.result_note || '', placeholder: 'Nhận xét kết quả (tùy chọn)' }),
      rating: h('select', { class: 'hub-input hub-small', 'aria-label': 'Đánh giá prompt' },
        h('option', { value: '', text: 'Chưa đánh giá' }), [1, 2, 3, 4, 5].map(n => h('option', { value: n, text: '★'.repeat(n), selected: p?.rating === n }))),
    };
    const msg = h('div', { role: 'status' });
    const submit = async e => {
      e.preventDefault();
      const payload = { title: f.title.value.trim(), goal: f.goal.value.trim(), body: f.body.value, variables: list(f.variables.value),
        models: list(f.models.value), tags: list(f.tags.value), result_note: f.result_note.value, rating: f.rating.value ? Number(f.rating.value) : null };
      try {
        const saved = p ? await api('PATCH', `/prompts/${p.id}`, payload) : await api('POST', '/prompts', payload);
        state.editingPrompt = saved; toast(msg, p ? `Đã lưu (phiên bản ${saved.version}).` : 'Đã tạo prompt.');
        renderPrompts();
      } catch (err) { toast(msg, err.message, true); }
    };
    return h('form', { class: 'hub-form', onsubmit: submit },
      h('label', { class: 'hub-field' }, 'Tiêu đề', f.title), h('label', { class: 'hub-field' }, 'Mục tiêu', f.goal),
      h('label', { class: 'hub-field' }, 'Nội dung (dùng {{tên_biến}} cho biến đầu vào)', f.body),
      h('div', { class: 'hub-grid2' }, h('label', { class: 'hub-field' }, 'Biến đầu vào (phân tách bằng dấu phẩy)', f.variables), h('label', { class: 'hub-field' }, 'Model tương thích', f.models)),
      h('div', { class: 'hub-grid2' }, h('label', { class: 'hub-field' }, 'Tag', f.tags), h('label', { class: 'hub-field' }, 'Đánh giá', f.rating)),
      h('label', { class: 'hub-field' }, 'Kết quả / nhận xét', f.result_note),
      h('div', { class: 'hub-row' }, h('button', { class: 'hub-btn primary', type: 'submit', text: p ? 'Lưu (tạo phiên bản nếu nội dung đổi)' : 'Tạo prompt' }),
        p ? h('button', { class: 'hub-btn', type: 'button', text: 'Sao chép nội dung', onclick: () => navigator.clipboard?.writeText(p.body).then(() => toast(msg, 'Đã sao chép.')).catch(() => toast(msg, 'Trình duyệt không cho phép sao chép.', true)) }) : null,
        p ? h('button', { class: 'hub-btn danger', type: 'button', text: 'Xóa prompt', onclick: async () => { if (!confirm('Xóa prompt này và toàn bộ lịch sử phiên bản?')) return; await api('DELETE', `/prompts/${p.id}`); state.editingPrompt = null; renderPrompts(); } }) : null,
        p ? h('button', { class: 'hub-btn', type: 'button', text: 'Tạo mới', onclick: () => { state.editingPrompt = null; renderPrompts(); } }) : null),
      msg);
  }

  async function renderPrompts() {
    const search = $('lib-search-prompt')?.value || '';
    const data = await api('GET', '/prompts?' + new URLSearchParams(search ? { q: search } : {}).toString());
    const cur = state.editingPrompt ? await api('GET', `/prompts/${state.editingPrompt.id}`).catch(() => null) : null;
    const listNode = h('ul', { class: 'hub-items' }, data.items.map(p => h('li', { class: p.id === cur?.id ? 'active' : '' },
      h('button', { type: 'button', class: 'hub-linkbtn', text: p.title, onclick: () => { state.editingPrompt = p; renderPrompts(); } }),
      h('span', { class: 'ov-muted', text: ` v${p.version} · ${p.tags.join(', ')}` }))));
    const history = cur ? h('details', { class: 'hub-details' }, h('summary', { text: `Lịch sử phiên bản (${cur.versions.length})` }),
      h('ol', {}, cur.versions.map(v => h('li', {}, h('span', { text: `v${v.version} · ${fmtDate(v.created)}` }),
        h('details', {}, h('summary', { text: 'Xem nội dung' }), h('pre', { class: 'hub-pre', text: v.body })))))) : null;
    body().replaceChildren(
      h('div', { class: 'hub-split' },
        h('section', { class: 'hub-pane' },
          h('div', { class: 'hub-row' }, h('input', { id: 'lib-search-prompt', type: 'search', class: 'hub-input', placeholder: 'Tìm prompt, tag…', value: search, 'aria-label': 'Tìm prompt',
            onchange: () => renderPrompts() }), h('button', { class: 'hub-btn', type: 'button', text: '+ Prompt mới', onclick: () => { state.editingPrompt = null; renderPrompts(); } })),
          data.items.length ? listNode : h('p', { class: 'ov-muted', text: 'Chưa có prompt. Tạo mới hoặc nhập từ Markdown ở tab Nguồn & cài đặt.' })),
        h('section', { class: 'hub-pane' }, h('h3', { text: cur ? `Sửa: ${cur.title}` : 'Prompt mới' }), promptForm(cur), history)));
  }

  /* ---------- Memory Vault ---------- */
  function memoryForm(m) {
    const f = {
      kind: h('select', { class: 'hub-input', 'aria-label': 'Loại memory' }, Object.entries(MEM_KIND).map(([v, t]) => h('option', { value: v, text: t, selected: (m?.kind || 'note') === v }))),
      title: h('input', { class: 'hub-input', maxlength: 160, value: m?.title || '', 'aria-label': 'Tiêu đề memory' }),
      body: h('textarea', { class: 'hub-input', rows: 6, maxlength: 10000, text: m?.body || '', 'aria-label': 'Nội dung memory' }),
      tags: h('input', { class: 'hub-input', value: (m?.tags || []).join(', '), 'aria-label': 'Tag memory' }),
      source: h('input', { class: 'hub-input', maxlength: 300, value: m?.source || '', placeholder: 'Nguồn, ví dụ: link, cuộc trò chuyện ngày 01/10', 'aria-label': 'Nguồn' }),
      confidence: h('select', { class: 'hub-input', 'aria-label': 'Độ tin cậy' }, [['low', 'Thấp'], ['medium', 'Trung bình'], ['high', 'Cao']].map(([v, t]) => h('option', { value: v, text: t, selected: (m?.confidence || 'medium') === v }))),
      valid_from: h('input', { class: 'hub-input', type: 'date', value: m?.valid_from || '', 'aria-label': 'Ngày hiệu lực' }),
      review_at: h('input', { class: 'hub-input', type: 'date', value: m?.review_at || '', 'aria-label': 'Ngày xem lại' }),
      pinned: h('input', { type: 'checkbox', checked: !!m?.pinned }),
      stale: h('input', { type: 'checkbox', checked: !!m?.stale }),
    };
    const msg = h('div', { role: 'status' });
    return h('form', { class: 'hub-form', onsubmit: async e => {
      e.preventDefault();
      const payload = { kind: f.kind.value, title: f.title.value.trim(), body: f.body.value, tags: list(f.tags.value), source: f.source.value.trim(),
        confidence: f.confidence.value, valid_from: f.valid_from.value, review_at: f.review_at.value, pinned: f.pinned.checked, stale: f.stale.checked };
      try {
        const saved = m ? await api('PATCH', `/memories/${m.id}`, payload) : await api('POST', '/memories', payload);
        state.editingMemory = saved; renderMemories();
      } catch (err) { toast(msg, err.message, true); }
    } },
      h('div', { class: 'hub-grid2' }, h('label', { class: 'hub-field' }, 'Loại', f.kind), h('label', { class: 'hub-field' }, 'Độ tin cậy', f.confidence)),
      h('label', { class: 'hub-field' }, 'Tiêu đề', f.title), h('label', { class: 'hub-field' }, 'Nội dung', f.body),
      h('div', { class: 'hub-grid2' }, h('label', { class: 'hub-field' }, 'Tag', f.tags), h('label', { class: 'hub-field' }, 'Nguồn', f.source)),
      h('div', { class: 'hub-grid2' }, h('label', { class: 'hub-field' }, 'Ngày hiệu lực', f.valid_from), h('label', { class: 'hub-field' }, 'Ngày xem lại', f.review_at)),
      h('div', { class: 'hub-row' }, h('label', { class: 'hub-check' }, f.pinned, ' Ghim'), h('label', { class: 'hub-check' }, f.stale, ' Đã lỗi thời')),
      h('div', { class: 'hub-row' }, h('button', { class: 'hub-btn primary', type: 'submit', text: m ? 'Lưu' : 'Thêm memory' }),
        m ? h('button', { class: 'hub-btn danger', type: 'button', text: 'Xóa', onclick: async () => { if (!confirm('Xóa memory này?')) return; await api('DELETE', `/memories/${m.id}`); state.editingMemory = null; renderMemories(); } }) : null,
        m ? h('button', { class: 'hub-btn', type: 'button', text: 'Mới', onclick: () => { state.editingMemory = null; renderMemories(); } }) : null),
      msg);
  }

  async function renderMemories() {
    const f = state.memFilter;
    const params = new URLSearchParams();
    if (f.q) params.set('q', f.q); if (f.kind) params.set('kind', f.kind);
    if (f.stale) params.set('stale', '1'); if (f.pinned) params.set('pinned', '1');
    const data = await api('GET', '/memories?' + params.toString());
    const dups = await api('GET', '/memories/duplicates');
    const filter = h('form', { class: 'hub-row', onsubmit: e => { e.preventDefault(); f.q = e.target.q.value.trim(); renderMemories(); } },
      h('input', { name: 'q', class: 'hub-input', type: 'search', value: f.q, placeholder: 'Tìm trong nội dung, tag, nguồn…', 'aria-label': 'Tìm memory' }),
      h('select', { class: 'hub-input hub-small', 'aria-label': 'Lọc loại', onchange: e => { f.kind = e.target.value; renderMemories(); } },
        h('option', { value: '', text: 'Mọi loại' }), Object.entries(MEM_KIND).map(([v, t]) => h('option', { value: v, text: t, selected: f.kind === v }))),
      h('label', { class: 'hub-check' }, h('input', { type: 'checkbox', checked: f.pinned, onchange: e => { f.pinned = e.target.checked; renderMemories(); } }), ' Đã ghim'),
      h('label', { class: 'hub-check' }, h('input', { type: 'checkbox', checked: f.stale, onchange: e => { f.stale = e.target.checked; renderMemories(); } }), ' Đã lỗi thời'),
      h('button', { class: 'hub-btn', type: 'submit', text: 'Tìm' }));
    const items = data.items.map(m => h('li', { class: 'hub-memitem' + (m.stale ? ' is-stale' : '') },
      h('button', { type: 'button', class: 'hub-linkbtn', onclick: () => { state.editingMemory = m; renderMemories(); } },
        m.pinned ? '📌 ' : '', m.title),
      h('span', { class: 'hub-badge', text: MEM_KIND[m.kind] || m.kind }),
      m.stale ? h('span', { class: 'hub-badge warn', text: 'Lỗi thời' }) : null,
      m.review_due ? h('span', { class: 'hub-badge warn', text: 'Cần xem lại' }) : null,
      h('p', { class: 'ov-muted', text: [m.tags.length ? m.tags.join(', ') : null, m.source ? 'Nguồn: ' + m.source : null, 'Độ tin cậy: ' + ({ low: 'thấp', medium: 'trung bình', high: 'cao' }[m.confidence] || m.confidence), `cập nhật ${fmtDate(m.updated)}`].filter(Boolean).join(' · ') })));
    const dupBlock = dups.groups.length ? h('details', { class: 'hub-details', open: true },
      h('summary', { text: `Có ${dups.groups.length} nhóm memory trùng tiêu đề` }),
      dups.groups.map(group => h('div', { class: 'hub-dup' },
        h('p', { text: `“${group[0].title}” — ${group.length} bản` }),
        h('button', { type: 'button', class: 'hub-btn', text: 'Gộp vào bản cũ nhất (cần xác nhận)', onclick: async () => {
          if (!confirm(`Gộp ${group.length - 1} bản vào “${group[0].title}”? Nội dung các bản sẽ được nối vào bản giữ lại, bản cũ bị xóa.`)) return;
          await api('POST', '/memories/merge', { keep: group[0].id, remove: group.slice(1).map(x => x.id), confirm: true });
          renderMemories();
        } })))) : null;
    body().replaceChildren(
      h('div', { class: 'hub-split' },
        h('section', { class: 'hub-pane' }, filter, h('p', { class: 'ov-muted', text: `${data.items.length} memory` }), h('ul', { class: 'hub-items' }, items), dupBlock),
        h('section', { class: 'hub-pane' }, h('h3', { text: state.editingMemory ? 'Sửa memory' : 'Memory mới' }), memoryForm(state.editingMemory))));
  }

  /* ---------- Nguồn & cài đặt ---------- */
  async function renderSources() {
    const [src, settings, aiStatus] = await Promise.all([api('GET', '/sources'), api('GET', '/settings'), api('GET', '/ai/status')]);
    const aiModels = await api('GET', '/ai/models').catch(() => ({ models: [], selected: aiStatus.model || 'auto' }));
    const srcList = h('ul', { class: 'hub-items' }, src.items.map(s => h('li', {},
      h('label', { class: 'hub-check' }, h('input', { type: 'checkbox', checked: s.enabled, onchange: async e => { await api('PATCH', `/sources/${s.id}`, { enabled: e.target.checked }); renderSources(); } }), ' ', s.name),
      h('span', { class: 'ov-muted', text: ` · ${s.type} · ${s.url} · ok ${s.ok_count} / lỗi ${s.fail_count}${s.last_error ? ' · lỗi gần nhất: ' + s.last_error : ''}` }),
      h('button', { type: 'button', class: 'hub-linkbtn danger', text: 'Xóa', onclick: async () => { if (!confirm(`Xóa nguồn “${s.name}”?`)) return; await api('DELETE', `/sources/${s.id}`); renderSources(); } }))));
    const rssName = h('input', { class: 'hub-input', maxlength: 80, placeholder: 'Tên nguồn', 'aria-label': 'Tên nguồn RSS' });
    const rssUrl = h('input', { class: 'hub-input', type: 'url', placeholder: 'https://… (RSS hoặc Atom)', 'aria-label': 'URL RSS' });
    const ownerLogin = h('input', { class: 'hub-input', maxlength: 39, placeholder: 'Tên tài khoản/tổ chức GitHub', 'aria-label': 'Tên GitHub' });
    const addMsg = h('div', { role: 'status' });
    const addForms = h('div', { class: 'hub-grid2' },
      h('form', { class: 'hub-form', onsubmit: async e => { e.preventDefault(); try { await api('POST', '/sources', { type: 'rss', name: rssName.value, url: rssUrl.value }); renderSources(); } catch (err) { toast(addMsg, err.message, true); } } },
        h('h4', { text: 'Thêm nguồn RSS/Atom' }), rssName, rssUrl, h('button', { class: 'hub-btn', type: 'submit', text: 'Thêm RSS' })),
      h('form', { class: 'hub-form', onsubmit: async e => { e.preventDefault(); try { await api('POST', '/sources', { type: 'github_owner', login: ownerLogin.value.trim() }); renderSources(); } catch (err) { toast(addMsg, err.message, true); } } },
        h('h4', { text: 'Theo dõi tác giả/tổ chức GitHub' }), ownerLogin, h('button', { class: 'hub-btn', type: 'submit', text: 'Theo dõi' })));

    const s = settings;
    const wName = h('input', { class: 'hub-input', maxlength: 60, value: s.weather.name, 'aria-label': 'Tên địa điểm thời tiết' });
    const wLat = h('input', { class: 'hub-input', type: 'number', step: 'any', min: -90, max: 90, value: s.weather.latitude, 'aria-label': 'Vĩ độ' });
    const wLon = h('input', { class: 'hub-input', type: 'number', step: 'any', min: -180, max: 180, value: s.weather.longitude, 'aria-label': 'Kinh độ' });
    const wTz = h('input', { class: 'hub-input', value: s.weather.timezone, 'aria-label': 'Múi giờ thời tiết' });
    const en = h('input', { type: 'checkbox', checked: s.schedule.enabled });
    const aiAuto = h('input', { type: 'checkbox', checked: s.schedule.ai_enabled !== false });
    const time = h('input', { class: 'hub-input', type: 'time', value: s.schedule.time, 'aria-label': 'Giờ chạy' });
    const tz = h('input', { class: 'hub-input', value: s.schedule.timezone, 'aria-label': 'Múi giờ' });
    const topics = h('input', { class: 'hub-input', value: s.github.topics.join(', '), 'aria-label': 'Topic GitHub' });
    const perTopic = h('input', { class: 'hub-input hub-small', type: 'number', min: 1, max: 100, value: s.github.per_topic_limit });
    const releases = h('input', { class: 'hub-input hub-small', type: 'number', min: 0, max: 100, value: s.github.release_checks });
    const refresh = h('input', { class: 'hub-input hub-small', type: 'number', min: 0, max: 200, value: s.github.refresh_limit });
    const maxReq = h('input', { class: 'hub-input hub-small', type: 'number', min: 1, max: 1000, value: s.max_requests_per_run });
    const bEnabled = h('input', { type: 'checkbox', checked: s.backup.enabled });
    const bKeep = h('input', { class: 'hub-input hub-small', type: 'number', min: 1, max: 60, value: s.backup.keep });
    const setMsg = h('div', { role: 'status' });
    const aiKey = h('input', { class: 'hub-input', type: 'password', maxlength: 512, autocomplete: 'off', placeholder: 'Dán unified API key từ FreeLLMAPI Desktop', 'aria-label': 'Unified API key FreeLLMAPI' });
    const aiMsg = h('div', { role: 'status' });
    const aiStatusText = aiStatus.available ? `Đã kết nối · model ${aiStatus.model}` : aiStatus.configured ? aiStatus.message : 'Chưa kết nối. Mở FreeLLMAPI Desktop rồi dán unified API key.';
    let selectedModel = aiModels.selected || 'auto';
    const modelCounts = {
      errors: aiModels.models.filter(m => m.errors > 0).length,
      ok: aiModels.models.filter(m => m.state === 'ok').length,
      unknown: aiModels.models.filter(m => m.state === 'unknown').length,
      rateLimited: aiModels.models.filter(m => m.rate_limits > 0).length,
      temporary: aiModels.models.filter(m => m.temporary_failures > 0).length,
      tested: aiModels.models.filter(m => m.probe?.state === 'ok').length,
    };
    const modelFilter = h('select', { class: 'hub-input', 'aria-label': 'Lọc model theo trạng thái API' },
      h('option', { value: 'all', text: `Tất cả trạng thái (${aiModels.models.length})` }),
      h('option', { value: 'error', text: `Lỗi API không phải rate limit (${modelCounts.errors})` }),
      h('option', { value: 'rate_limited', text: `Rate limit · giữ lại (${modelCounts.rateLimited})` }),
      h('option', { value: 'temporary', text: `Lỗi tạm thời · giữ lại (${modelCounts.temporary})` }),
      (modelCounts.testedOption = h('option', { value: 'test_ok', text: `Test nhanh thành công (${modelCounts.tested})` })),
      h('option', { value: 'ok', text: `Đang ổn (${modelCounts.ok})` }),
      h('option', { value: 'unknown', text: `Chưa có log 7 ngày (${modelCounts.unknown})` }));
    const modelSelect = h('select', { class: 'hub-input', 'aria-label': 'Chọn model AI', onchange: async e => {
      try { const result = await api('POST', '/ai/model', { model: e.target.value }); selectedModel = result.model; refillModels(); window.dispatchEvent(new Event('dailyhub:model-changed')); toast(aiMsg, `Đã chọn model ${result.model}. Các tác vụ AI tiếp theo sẽ dùng model này.`, false); }
      catch (err) { toast(aiMsg, err.message, true); }
    } });
    const modelStateLabel = m => m.probe?.state === 'ok' ? `test OK · ${m.probe.latency_ms} ms`
      : m.probe?.state === 'rate_limited' ? 'test bị rate limit · giữ lại'
        : m.probe?.state === 'temporary' ? 'test lỗi tạm thời · giữ lại'
          : m.probe?.state === 'error' ? 'test thất bại'
      : m.state === 'error' ? `fail chưa ổn · ${m.errors}`
      : m.state === 'rate_limited' ? `rate limit · giữ lại (${m.rate_limits})`
        : m.state === 'temporary' ? `lỗi tạm thời · giữ lại (${m.temporary_failures})`
        : m.state === 'warning' ? `${m.errors ? `có lỗi · ${m.errors}` : 'đang ổn'}${m.rate_limits ? ` · rate limit ${m.rate_limits}` : ''}${m.temporary_failures ? ` · tạm thời ${m.temporary_failures}` : ''}`
        : m.state === 'ok' ? `ổn · ${m.successes}/${m.attempts}`
          : 'chưa có log 7 ngày';
    const refillModels = () => {
      const filter = modelFilter.value;
      const matches = aiModels.models.filter(m => filter === 'all'
        || (filter === 'error' && m.errors > 0)
        || (filter === 'rate_limited' && m.rate_limits > 0)
        || (filter === 'temporary' && m.temporary_failures > 0)
        || (filter === 'test_ok' && m.probe?.state === 'ok') || m.state === filter);
      const options = [h('option', { value: 'auto', text: 'Auto · FreeLLMAPI tự chọn model khả dụng' })];
      if (selectedModel !== 'auto' && !matches.some(m => m.id === selectedModel)) {
        options.push(h('option', { value: selectedModel, text: `${selectedModel} · đang chọn (ngoài bộ lọc)` }));
      }
      options.push(...matches.map(m => h('option', { value: m.id,
        text: `${m.id} · ${modelStateLabel(m)}${m.provider ? ` · ${m.provider}` : ''}` })));
      modelSelect.replaceChildren(...options);
      modelSelect.value = selectedModel;
    };
    modelFilter.addEventListener('change', refillModels);
    refillModels();
    const aiTestOutput = h('div', { class: 'hub-ai-test-result', role: 'status', 'aria-live': 'polite' });
    const aiTestButton = h('button', { class: 'hub-btn', type: 'button', text: 'Test nhanh model', onclick: async e => {
      const button = e.currentTarget;
      button.disabled = true;
      button.textContent = 'Đang gọi AI…';
      aiTestOutput.replaceChildren(h('p', { class: 'ov-muted', text: 'Đang gửi một prompt test rất ngắn.' }));
      try {
        const result = await api('POST', '/ai/test-model', { model: selectedModel });
        const probe = { state: result.ok ? 'ok' : result.failure_kind, latency_ms: result.latency_ms, checked_at: Math.floor(Date.now() / 1000) };
        if (selectedModel === 'auto') aiModels.auto_probe = probe;
        else {
          const item = aiModels.models.find(m => m.id === selectedModel);
          if (item) item.probe = probe;
        }
        modelCounts.tested = aiModels.models.filter(m => m.probe?.state === 'ok').length;
        modelCounts.testedOption.textContent = `Test nhanh thành công (${modelCounts.tested})`;
        refillModels();
        if (result.ok) {
          aiTestOutput.replaceChildren(h('p', { class: 'hub-ok', text: `Test thành công · ${result.model} · ${result.latency_ms} ms` }),
            h('p', { text: `AI trả lời: ${result.answer}` }));
        } else {
          aiTestOutput.replaceChildren(h('p', { class: 'hub-error', text: `Test chưa thành công · ${result.failure_kind} · ${result.message}` }));
        }
      } catch (err) { aiTestOutput.replaceChildren(h('p', { class: 'hub-error', text: err.message })); }
      finally { button.disabled = false; button.textContent = 'Test nhanh model'; }
    } });
    const priorProbe = selectedModel === 'auto' ? aiModels.auto_probe : aiModels.models.find(m => m.id === selectedModel)?.probe;
    if (priorProbe) aiTestOutput.replaceChildren(h('p', { class: priorProbe.state === 'ok' ? 'hub-ok' : 'ov-muted',
      text: priorProbe.state === 'ok' ? `Test gần nhất thành công · ${priorProbe.latency_ms} ms` : `Kết quả test gần nhất: ${priorProbe.state}` }));
    const aiForm = h('form', { class: 'hub-form', onsubmit: async e => {
      e.preventDefault();
      try {
        const result = await api('POST', '/ai/config', { api_key: aiKey.value });
        aiKey.value = '';
        toast(aiMsg, result.available ? `Đã kết nối FreeLLMAPI · model ${result.model}. Tin sẽ được AI xử lý tự động.` : 'Đã lưu khóa nhưng FreeLLMAPI chưa sẵn sàng.', !result.available);
        renderSources();
      } catch (err) { toast(aiMsg, err.message, true); }
    } },
      h('h4', { text: 'AI tự động · FreeLLMAPI' }),
      h('p', { class: aiStatus.available ? 'hub-ok' : 'hub-error', text: aiStatusText }),
      h('p', { class: 'ov-muted', text: `Router: ${aiStatus.base_url || 'http://127.0.0.1:31415/v1'} · khóa được mã hóa bằng Windows DPAPI trên máy này, không đưa vào bản sao lưu.` }),
      h('label', { class: 'hub-field' }, 'Lọc trạng thái API (log 7 ngày)', modelFilter),
      h('label', { class: 'hub-field' }, `Model (${aiModels.models.length} model trong catalog)`, modelSelect),
      aiTestButton, aiTestOutput,
      aiModels.models.length ? h('p', { class: 'ov-muted', text: `Test nhanh chỉ gửi một request tối đa 24 token; câu trả lời hiện ngay bên dưới. Dùng bộ lọc “Test nhanh thành công” để xem model đã chạy được. Rate limit/lỗi tạm thời được giữ; model đã tắt, không quota hoặc lỗi định danh/model lặp lại bị loại (${aiModels.removed?.disabled || 0} đã tắt, ${aiModels.removed?.removed || 0} lỗi).` }) : null,
      h('label', { class: 'hub-field' }, 'Unified API key', aiKey),
      h('button', { class: 'hub-btn primary', type: 'submit', text: 'Kết nối AI' }), aiMsg);
    const settingsForm = h('form', { class: 'hub-form', onsubmit: async e => {
      e.preventDefault();
      const payload = { schedule: { enabled: en.checked, time: time.value, timezone: tz.value.trim(), ai_enabled: aiAuto.checked },
        weather: { name: wName.value.trim(), latitude: Number(wLat.value), longitude: Number(wLon.value), timezone: wTz.value.trim() },
        github: { topics: list(topics.value), per_topic_limit: Number(perTopic.value), release_checks: Number(releases.value), refresh_limit: Number(refresh.value) },
        max_requests_per_run: Number(maxReq.value), backup: { enabled: bEnabled.checked, keep: Number(bKeep.value) } };
      try { await api('PUT', '/settings', payload); toast(setMsg, 'Đã lưu cài đặt. Lịch mới áp dụng ngay.'); renderSources(); }
      catch (err) { toast(setMsg, err.message, true); }
    } },
      h('h4', { text: 'Lịch chạy tự động' }),
      h('label', { class: 'hub-check' }, en, ' Bật chạy mỗi ngày'),
      h('label', { class: 'hub-check' }, aiAuto, ` AI tự xếp hạng tài nguyên mới/cập nhật bằng model đang chọn (${aiStatus.model || 'Auto'})`),
      h('div', { class: 'hub-grid2' }, h('label', { class: 'hub-field' }, 'Giờ chạy', time), h('label', { class: 'hub-field' }, 'Múi giờ (ví dụ Asia/Ho_Chi_Minh)', tz)),
      h('h4', { text: 'Sao lưu tự động' }),
      h('label', { class: 'hub-check' }, bEnabled, ' Tự sao lưu sau mỗi lần đồng bộ hằng ngày'),
      h('label', { class: 'hub-field' }, 'Số bản sao lưu giữ lại (1–60)', bKeep),
      h('h4', { text: 'Thời tiết (Open-Meteo, không cần key)' }),
      h('div', { class: 'hub-grid2' },
        h('label', { class: 'hub-field' }, 'Tên địa điểm', wName),
        h('label', { class: 'hub-field' }, 'Múi giờ', wTz),
        h('label', { class: 'hub-field' }, 'Vĩ độ', wLat),
        h('label', { class: 'hub-field' }, 'Kinh độ', wLon)),
      h('h4', { text: 'Nguồn GitHub theo topic' }),
      h('label', { class: 'hub-field' }, 'Topic (phân tách bằng dấu phẩy, chữ thường)', topics),
      h('div', { class: 'hub-grid2' },
        h('label', { class: 'hub-field' }, 'Số repo mỗi topic', perTopic),
        h('label', { class: 'hub-field' }, 'Số repo kiểm tra release', releases),
        h('label', { class: 'hub-field' }, 'Số repo đã theo dõi cần làm mới mỗi lần', refresh),
        h('label', { class: 'hub-field' }, 'Giới hạn request GitHub mỗi lần chạy', maxReq)),
      h('p', { class: 'ov-muted', text: s.github_token_configured ? 'GitHub token: đã cấu hình trên máy chủ (không hiển thị).' : 'GitHub token: chưa cấu hình. Không bắt buộc, nhưng không có token thì hạn mức API rất thấp. Đặt biến môi trường GITHUB_TOKEN trên máy chạy server rồi khởi động lại.' }),
      h('button', { class: 'hub-btn primary', type: 'submit', text: 'Lưu cài đặt' }), setMsg);

    body().replaceChildren(
      h('section', { class: 'hub-pane' }, h('h3', { text: 'Nguồn dữ liệu' }),
        h('p', { class: 'ov-muted', text: 'Chỉ dùng API/RSS công khai và chính thức. Không thu thập nội dung cần đăng nhập, không vượt CAPTCHA.' }),
        srcList, addForms, addMsg),
      h('section', { class: 'hub-pane' }, aiForm),
      h('section', { class: 'hub-pane' }, settingsForm),
      h('section', { class: 'hub-pane' }, backupPanel()));
  }

  async function loadBackups(box) {
    const note = (msg, isError) => box.append(h('p', { class: isError ? 'hub-error' : 'hub-ok', role: 'status', text: msg }));
    try {
      const { items, settings } = await api('GET', '/backups');
      box.replaceChildren(
        h('p', { class: 'ov-muted', text: settings.enabled
          ? `Tự sao lưu sau mỗi lần đồng bộ hằng ngày. Giữ ${settings.keep} bản gần nhất.`
          : 'Tự sao lưu đang tắt. Bật trong phần cài đặt bên dưới.' }),
        items.length ? h('ul', { class: 'hub-items' }, items.map(b => h('li', {},
          h('span', { text: `${b.name} · ${Math.max(1, Math.round(b.size / 1024))} KB · ${fmtDate(Date.parse(b.created))}` }),
          h('button', { class: 'hub-linkbtn', type: 'button', text: 'Khôi phục', onclick: async () => {
            if (!confirm('Khôi phục sẽ GỘP bản sao lưu này vào dữ liệu hiện có. Dữ liệu đang có không bị xóa hay ghi đè. Tiếp tục?')) return;
            try { const data = await api('GET', '/backups/' + encodeURIComponent(b.name)); const r = await api('POST', '/import', data);
              loadBackups(box); note(`Đã khôi phục: ${r.memories_added} memory, ${r.prompts_added} prompt mới.`); }
            catch (err) { note(err.message, true); } } }),
          h('button', { class: 'hub-linkbtn danger', type: 'button', text: 'Xóa', onclick: async () => {
            if (!confirm(`Xóa bản sao lưu ${b.name}?`)) return;
            await api('DELETE', '/backups/' + encodeURIComponent(b.name)); loadBackups(box); } })))) : h('p', { class: 'ov-muted', text: 'Chưa có bản sao lưu nào.' }),
        h('button', { class: 'hub-btn', type: 'button', text: 'Tạo bản sao lưu ngay', onclick: async () => {
          try { await api('POST', '/backups'); loadBackups(box); } catch (err) { note(err.message, true); } } }));
    } catch (err) { box.replaceChildren(h('p', { class: 'hub-error', text: err.message })); }
  }

  function backupPanel() {
    const out = h('div', { role: 'status' });
    const bkBox = h('div', { class: 'hub-form' });
    loadBackups(bkBox);
    const file = h('input', { type: 'file', accept: 'application/json,.json', 'aria-label': 'Chọn tệp sao lưu JSON' });
    const md = h('textarea', { class: 'hub-input', rows: 6, placeholder: '## Tiêu đề\nNội dung…\n## Tiêu đề khác\nNội dung…', 'aria-label': 'Nội dung Markdown để nhập' });
    const mdKind = h('select', { class: 'hub-input hub-small', 'aria-label': 'Nhập thành' }, h('option', { value: 'memory', text: 'Memory' }), h('option', { value: 'prompt', text: 'Prompt' }));
    const resetScope = h('select', { class: 'hub-input hub-small', 'aria-label': 'Phạm vi xóa' }, h('option', { value: 'personal', text: 'Chỉ dữ liệu cá nhân (memory, prompt, ghi chú tài nguyên)' }), h('option', { value: 'all', text: 'Toàn bộ, kể cả tài nguyên đã đồng bộ' }));
    const resetConfirm = h('input', { class: 'hub-input hub-small', placeholder: 'Gõ XOA để xác nhận', 'aria-label': 'Xác nhận xóa' });
    return h('div', { class: 'hub-form' },
      h('h3', { text: 'Sao lưu & khôi phục' }),
      h('p', { class: 'ov-muted', text: 'Bản sao lưu gồm memory, prompt (kèm lịch sử phiên bản), trạng thái tài nguyên (đã lưu, đã xem, đánh giá, ghi chú), nguồn và cài đặt. Không gồm GitHub token.' }),
      bkBox,
      h('div', { class: 'hub-row' }, h('button', { class: 'hub-btn', type: 'button', text: 'Xuất bản sao lưu (JSON)', onclick: async () => {
        try { download(`dailyhub-library-${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(await api('GET', '/export'), null, 2)); toast(out, 'Đã tạo tệp sao lưu.'); }
        catch (err) { toast(out, err.message, true); } } })),
      h('div', { class: 'hub-row' }, file, h('button', { class: 'hub-btn', type: 'button', text: 'Nhập bản sao lưu', onclick: async () => {
        const f = file.files[0]; if (!f) return toast(out, 'Chọn một tệp JSON trước.', true);
        if (f.size > 20 * 1024 * 1024) return toast(out, 'Tệp quá lớn (tối đa 20 MB).', true);
        try { const data = JSON.parse(await f.text()); const r = await api('POST', '/import', data);
          toast(out, `Đã nhập: ${r.memories_added} memory mới (${r.memories_skipped} bỏ qua vì trùng), ${r.prompts_added} prompt mới, ${r.states_applied} trạng thái tài nguyên, ${r.sources_added} nguồn.`); }
        catch (err) { toast(out, err.message, true); } } })),
      h('h4', { text: 'Nhập từ Markdown' }),
      h('p', { class: 'ov-muted', text: 'Mỗi mục bắt đầu bằng “## Tiêu đề”. Nội dung bên dưới sẽ thành phần mô tả.' }),
      md, h('div', { class: 'hub-row' }, mdKind, h('button', { class: 'hub-btn', type: 'button', text: 'Nhập Markdown', onclick: async () => {
        try { const r = await api('POST', '/import/markdown', { kind: mdKind.value, text: md.value }); toast(out, `Đã tạo ${r.created} mục.`); md.value = ''; }
        catch (err) { toast(out, err.message, true); } } })),
      h('h4', { text: 'Xóa dữ liệu' }),
      h('div', { class: 'hub-row' }, resetScope, resetConfirm, h('button', { class: 'hub-btn danger', type: 'button', text: 'Xóa', onclick: async () => {
        if (!confirm('Thao tác này không thể hoàn tác. Tiếp tục?')) return;
        try { await api('POST', '/data/reset', { scope: resetScope.value, confirm: resetConfirm.value }); toast(out, 'Đã xóa.'); renderSources(); }
        catch (err) { toast(out, err.message, true); } } })),
      out);
  }

  /* ---------- Tìm kiếm chung ---------- */
  function searchGroup(title, count, nodes) {
    if (!count) return null;
    return h('div', { class: 'hub-dup' }, h('h4', { text: `${title} (${count})` }), ...nodes);
  }
  function openIn(tab) { state.tab = tab; renderTabs(); openTab(); }
  function searchResults(r) {
    if (!r.total) return [h('p', { class: 'ov-muted', text: `Không có kết quả cho “${r.query}”.` })];
    const res = r.resources.map(x => h('div', { class: 'hub-row' },
      h('span', { class: 'hub-badge kind-' + x.kind, text: KIND[x.kind] || x.kind }),
      h('strong', { text: x.name }), h('span', { class: 'ov-muted', text: x.snippet || '' }),
      h('button', { class: 'hub-linkbtn', type: 'button', text: 'Mở trong Tài nguyên', onclick: () => { state.res.q = x.name; state.res.offset = 0; openIn('resources'); } })));
    const mems = r.memories.map(m => h('div', { class: 'hub-row' },
      h('strong', { text: m.title }), h('span', { class: 'ov-muted', text: m.snippet || '' }),
      h('button', { class: 'hub-linkbtn', type: 'button', text: 'Mở memory', onclick: async () => { state.editingMemory = await api('GET', '/memories/' + m.id); openIn('memory'); } })));
    const prompts = r.prompts.map(p => h('div', { class: 'hub-row' },
      h('strong', { text: p.title }), h('span', { class: 'ov-muted', text: p.snippet || '' }),
      h('button', { class: 'hub-linkbtn', type: 'button', text: 'Mở prompt', onclick: async () => { state.editingPrompt = await api('GET', '/prompts/' + p.id); openIn('prompts'); } })));
    const notes = r.notes.map(n => h('div', { class: 'hub-row' },
      h('strong', { text: n.name }), h('span', { class: 'ov-muted', text: 'Ghi chú: ' + (n.snippet || '') }),
      h('button', { class: 'hub-linkbtn', type: 'button', text: 'Mở tài nguyên', onclick: () => { state.res.q = n.name; state.res.offset = 0; openIn('resources'); } })));
    return [
      h('p', { class: 'ov-muted', text: `${r.total} kết quả; đã được AI xếp theo độ liên quan tới “${r.query}”.` }),
      searchGroup('Tài nguyên', r.resources.length, res),
      searchGroup('Memory', r.memories.length, mems),
      searchGroup('Prompt', r.prompts.length, prompts),
      searchGroup('Ghi chú tài nguyên', r.notes.length, notes),
    ].filter(Boolean);
  }
  async function renderSearch() {
    const input = h('input', { class: 'hub-input', type: 'search', maxlength: 100, value: state.searchQ,
      placeholder: 'Tìm trong tài nguyên, memory, prompt và ghi chú…', 'aria-label': 'Tìm kiếm chung' });
    const out = h('div', { class: 'hub-list', 'aria-live': 'polite' });
    let evidence = [];
    const askInput = h('input', { class: 'hub-input', maxlength: 500, placeholder: 'Hỏi về kết quả, ví dụ: mục nào phù hợp người mới?', 'aria-label': 'Câu hỏi cho AI' });
    const askOut = h('div', { class: 'hub-ai-answer', 'aria-live': 'polite' });
    const askBtn = h('button', { class: 'hub-btn primary', type: 'submit', text: 'Hỏi AI', disabled: true });
    const askForm = h('form', { class: 'hub-row', onsubmit: async e => {
      e.preventDefault();
      if (!evidence.length || !askInput.value.trim()) return;
      askBtn.disabled = true; askOut.replaceChildren(h('p', { class: 'ov-muted', text: 'AI đang đọc các kết quả liên quan…' }));
      try {
        const answer = await api('POST', '/ai/ask-library', { question: askInput.value.trim(), items: evidence });
        askOut.replaceChildren(h('p', { text: answer.answer }), answer.sources?.length
          ? h('small', { class: 'ov-muted', text: 'Căn cứ: ' + answer.sources.map(id => evidence.find(item => item.id === id)?.title || id).join(' · ') }) : null);
      } catch (err) { askOut.replaceChildren(h('p', { class: 'hub-error', role: 'alert', text: err.message })); }
      finally { askBtn.disabled = !evidence.length; }
    } }, askInput, askBtn);
    const run = async () => {
      state.searchQ = input.value.trim();
      if (state.searchQ.length < 2) { evidence = []; askBtn.disabled = true; out.replaceChildren(h('p', { class: 'ov-muted', text: 'Nhập ít nhất 2 ký tự.' })); return; }
      try {
        const results = await api('GET', '/search?q=' + encodeURIComponent(state.searchQ));
        const candidates = [
          ...results.resources.map(x => ({ ...x, type: 'resource', title: x.name, id: 'resource:' + x.id })),
          ...results.memories.map(x => ({ ...x, type: 'memory', id: 'memory:' + x.id })),
          ...results.prompts.map(x => ({ ...x, type: 'prompt', id: 'prompt:' + x.id })),
          ...results.notes.map(x => ({ ...x, type: 'note', title: x.name, id: 'note:' + x.resource_id }))
        ];
        evidence = candidates.slice(0, 20).map(x => ({ id: x.id, type: x.type, title: x.title || x.name, snippet: x.snippet || x.description || '' }));
        askBtn.disabled = !evidence.length;
        if (candidates.length) {
          try {
            const ranked = await api('POST', '/ai/rank-search', { query: state.searchQ, items: candidates });
            const order = new Map(ranked.ids.map((id, i) => [id, i]));
            results.resources.sort((a, b) => (order.get('resource:' + a.id) ?? 999) - (order.get('resource:' + b.id) ?? 999));
            results.memories.sort((a, b) => (order.get('memory:' + a.id) ?? 999) - (order.get('memory:' + b.id) ?? 999));
            results.prompts.sort((a, b) => (order.get('prompt:' + a.id) ?? 999) - (order.get('prompt:' + b.id) ?? 999));
            results.notes.sort((a, b) => (order.get('note:' + a.resource_id) ?? 999) - (order.get('note:' + b.resource_id) ?? 999));
          } catch { /* AI hỏi đáp riêng báo lỗi; tìm kiếm từ khóa vẫn dùng được. */ }
        }
        askOut.replaceChildren();
        out.replaceChildren(...searchResults(results));
      }
      catch (err) { out.replaceChildren(h('p', { class: 'hub-error', text: err.message })); }
    };
    body().replaceChildren(h('section', { class: 'hub-pane' },
      h('form', { class: 'hub-row', onsubmit: e => { e.preventDefault(); run(); } }, input, h('button', { class: 'hub-btn primary', type: 'submit', text: 'Tìm' })),
      h('section', { class: 'hub-ai-box' }, h('div', {}, h('h3', { text: 'Hỏi AI về thư viện' }), h('p', { class: 'ov-muted', text: 'AI trả lời dựa trên kết quả tìm kiếm hiện tại và nêu nguồn tham khảo.' })), askForm, askOut),
      out));
    if (state.searchQ.length >= 2) run();
  }

  /* ---------- Tabs ---------- */
  const TABS = { search: ['Tìm kiếm', renderSearch], resources: ['Tài nguyên', renderResources], prompts: ['Prompt', renderPrompts], memory: ['Bộ nhớ', renderMemories], sources: ['Nguồn & cài đặt', renderSources] };
  function renderTabs() {
    const bar = $('lib-tabs');
    bar.replaceChildren(...Object.entries(TABS).map(([key, [label]]) => h('button', { type: 'button', class: 'view-btn', 'aria-pressed': String(state.tab === key), text: label,
      'data-lib': key, onclick: () => { state.tab = key; renderTabs(); openTab(); } })));
  }
  async function openTab() {
    body().replaceChildren(h('p', { class: 'ov-muted', text: 'Đang tải…' }));
    try { await TABS[state.tab][1](); }
    catch (err) { body().replaceChildren(h('p', { class: 'hub-error', role: 'alert', text: err.message })); }
  }

  /* ---------- Dashboard (Tổng quan) ---------- */
  const card = (title, ...kids) => h('article', { class: 'ov-card' }, h('h3', { text: title }), ...kids);

  async function weatherCard() {
    try {
      const w = await api('GET', '/weather');
      if (!w.ok) {
        return card('Thời tiết', h('p', { class: 'hub-error', text: w.error }),
          h('p', { class: 'ov-muted', text: `Vị trí: ${w.location.name}. Có thể đổi trong Nguồn & cài đặt.` }));
      }
      const c = w.current, unit = c.units.temperature || '°C';
      return card(`Thời tiết · ${w.location.name}`,
        h('p', { class: 'hub-big', text: c.temperature == null ? '–' : `${c.temperature} ${unit}` }),
        h('p', { text: `${c.description || ''}${c.feels_like != null ? ` · cảm giác ${c.feels_like} ${unit}` : ''}` }),
        h('p', { class: 'ov-muted', text: `Độ ẩm ${c.humidity ?? '–'}% · gió ${c.wind_speed ?? '–'} ${c.units.wind || ''} · mưa hiện tại ${c.precipitation ?? 0} mm` }),
        w.upcoming.length ? h('ul', { class: 'hub-events' }, w.upcoming.map(u => h('li', {
          text: `${(u.time || '').slice(11, 16)}: ${u.temperature ?? '–'}${unit}, ${u.description}, khả năng mưa ${u.rain_probability ?? '–'}%` }))) : null,
        h('p', { class: 'ov-muted', text: [w.stale ? 'Không kết nối được, đang hiển thị dữ liệu lưu gần nhất.' : null, w.attribution].filter(Boolean).join(' ') }));
    } catch (err) {
      return card('Thời tiết', h('p', { class: 'hub-error', text: err.message }));
    }
  }
  async function renderDashboard() {
    const dash = $('hub-dash');
    if (!dash) return;
    try {
      const [s, weather, aiStatus] = await Promise.all([api('GET', '/status'), weatherCard(), api('GET', '/ai/status', undefined, 4500).catch(() => ({ available: false, message: 'FreeLLMAPI chưa phản hồi. Có thể kiểm tra trong cài đặt AI.' }))]);
      const sch = s.scheduler || {};
      const lr = s.last_run;
      const nowStatus = s.sync_running ? 'Đang đồng bộ…' : (lr ? { ok: 'Thành công', partial: 'Một phần (có nguồn lỗi)', failed: 'Thất bại', empty: 'Không có nguồn bật', running: 'Đang chạy' }[lr.status] || lr.status : 'Chưa chạy lần nào');
      const failing = s.sources.filter(x => x.fail_count > 0 && x.last_error);
      const events = s.events.filter(e => ['new', 'release', 'missing', 'archived', 'recovered'].includes(e.type)).slice(0, 10);
      const syncBtn = h('button', { class: 'hub-btn primary', type: 'button', disabled: s.sync_running, text: s.sync_running ? 'Đang chạy…' : 'Chạy ngay',
        onclick: async () => { try { await api('POST', '/sync'); renderDashboard(); } catch (err) { alert(err.message); } } });
      const aiSettingsBtn = h('button', { class: 'hub-btn', type: 'button', text: aiStatus.available ? 'Cấu hình model AI' : 'Kết nối AI', onclick: () => {
        document.querySelector('.view-btn[data-view="library"]')?.click();
        setTimeout(() => document.querySelector('#lib-tabs [data-lib="sources"]')?.click(), 0);
      } });
      dash.replaceChildren(
        weather,
        card('Đồng bộ', h('p', { class: 'hub-big', text: nowStatus }),
          s.sync_running ? h('div', { class: 'hub-progress', role: 'progressbar', 'aria-label': 'Đang đồng bộ' }) : null,
          h('p', { class: 'ov-muted', text: lr ? `Lần gần nhất: ${fmtDate(lr.finished || lr.started)} · trigger: ${lr.trigger === 'auto' ? 'tự động' : 'thủ công'} · yêu cầu GitHub: ${lr.stats?.github_requests ?? '–'}` : 'Chưa có lần đồng bộ nào.' }),
          lr?.stats?.ai_review ? h('p', { class: lr.stats.ai_review.status === 'ok' ? 'hub-ok' : 'ov-muted', text: lr.stats.ai_review.status === 'ok'
            ? `AI ${lr.stats.ai_review.model}: đã phân tích ${lr.stats.ai_review.analyzed} tài nguyên mới/cập nhật${lr.stats.ai_review.top?.length ? ` · nổi bật: ${lr.stats.ai_review.top.map(x => `${x.name} (${x.score})`).join(', ')}` : ''}`
            : `AI tự động: ${lr.stats.ai_review.message || lr.stats.ai_review.status}` }) : null,
          syncBtn),
        card('Lịch tự động', h('p', { text: sch.enabled ? `Mỗi ngày lúc ${sch.time} (${sch.timezone})` : 'Đã tắt' }),
          h('p', { class: 'ov-muted', text: sch.enabled ? `Lần chạy tiếp theo: ${sch.next_run ? new Date(sch.next_run).toLocaleString('vi-VN') : '–'}` : '' }),
          sch.timezone_note ? h('p', { class: 'hub-warn', text: sch.timezone_note }) : null,
          h('p', { class: 'ov-muted', text: sch.limitation })),
        card('AI đang dùng được ở đâu?',
          h('p', { class: aiStatus.available ? 'hub-ok' : 'hub-warn', text: aiStatus.available ? `Đang kết nối · ${aiStatus.model || 'Auto'}` : aiStatus.message || 'Chưa kết nối FreeLLMAPI.' }),
          h('ul', { class: 'hub-ai-uses' },
            h('li', { text: 'Tin tức: tự tóm tắt xu hướng, điểm nóng và bài đáng đọc; tìm tin theo ý nghĩa.' }),
            h('li', { text: 'Thư viện AI: tìm ngữ nghĩa, hỏi đáp có nguồn, chấm mức phù hợp của repo/skill.' }),
            h('li', { text: 'Nguồn & cài đặt: bật phân tích AI tự động sau mỗi lần đồng bộ theo lịch.' }),
            h('li', { text: 'Prompt Spider hiện là hoạt ảnh heuristic; không phải AI đang phân tích request.' })),
          h('p', { class: 'ov-muted', text: sch.enabled && sch.ai_enabled ? 'Đang bật: AI tuyển chọn tài nguyên mới sau mỗi lần đồng bộ.' : 'Phân tích AI theo lịch đang tắt.' }),
          h('div', { class: 'hub-row' },
            h('button', { class: 'hub-btn', type: 'button', text: 'Mở bản tin AI', onclick: () => {
              document.querySelector('.view-btn[data-view="news"]')?.click();
              setTimeout(() => document.querySelector('#channel-tabs [data-channel="all"]')?.click(), 0);
            } }),
            h('button', { class: 'hub-btn', type: 'button', text: 'Hỏi AI thư viện', onclick: () => {
              document.querySelector('.view-btn[data-view="library"]')?.click();
              setTimeout(() => document.querySelector('#lib-tabs [data-lib="search"]')?.click(), 0);
            } }), aiSettingsBtn)),
        card('Kho tài nguyên', h('p', { class: 'hub-big', text: fmtNum(s.counts.resources) }),
          h('p', { class: 'ov-muted', text: `${fmtNum(s.counts.repos)} repo · ${fmtNum(s.counts.skills)} skill · ${fmtNum(s.counts.toolkit)} toolkit · ${fmtNum(s.counts.articles)} bài viết` }),
          h('p', { class: 'ov-muted', text: `${fmtNum(s.counts.saved)} đã lưu · ${fmtNum(s.counts.memories)} memory · ${fmtNum(s.counts.prompts)} prompt` })),
        card('Nguồn lỗi', failing.length ? h('ul', {}, failing.map(x => h('li', { text: `${x.name}: ${x.last_error}` }))) : h('p', { class: 'ov-muted', text: 'Không có nguồn lỗi gần đây.' })),
        card('Thay đổi gần đây', events.length ? h('ul', { class: 'hub-events' }, events.map(e => h('li', {},
          h('strong', { text: EVENT_LABEL[e.type] || e.type }), ' ', e.resource_name || '', e.detail && e.detail !== e.resource_name ? ' — ' + e.detail : '',
          h('span', { class: 'ov-muted', text: ' · ' + fmtDate(e.created) })))) : h('p', { class: 'ov-muted', text: 'Chưa có sự kiện. Sự kiện như repo mới, release mới hay repo ngừng hoạt động sẽ hiện ở đây.' })),
        card('Trạng thái', h('p', { class: 'ov-muted', text: s.github_token_configured ? 'GitHub token: đã cấu hình' : 'GitHub token: chưa cấu hình (hạn mức thấp)' }),
          h('p', { class: 'ov-muted', text: `Nguồn đang bật: ${s.sources.length}` })));
      if (s.sync_running) setTimeout(() => renderDashboard(), 3000);
    } catch (err) {
      dash.replaceChildren(card('Thư viện AI', h('p', { class: 'hub-error', text: err.message })));
    }
  }

  /* ---------- Khởi tạo ---------- */
  const libView = $('library-view');
  let dashTimer = null;
  const observer = new MutationObserver(() => {
    const view = document.body.dataset.view;
    clearInterval(dashTimer); dashTimer = null;
    if (view === 'overview') dashTimer = setInterval(() => { if (!document.hidden && document.body.dataset.view === 'overview') renderDashboard(); }, 60 * 1000);
    if (view === 'library') {
      if (!libView.dataset.loaded) { libView.dataset.loaded = '1'; renderTabs(); }
      openTab();
    }
    if (view === 'overview') renderDashboard();
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['data-view'] });
  window.addEventListener('dailyhub:imported', () => { if (document.body.dataset.view === 'library') openTab(); });
  window.addEventListener('dailyhub:model-changed', () => {
    state.aiResourceRanks.clear(); aiRankError = ''; aiRankRetryAfter = 0;
    if (document.body.dataset.view === 'library' && state.tab === 'resources') renderResources();
  });
})();
