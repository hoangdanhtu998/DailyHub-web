/* Khu vực Tổng quan: đồng hồ, tin chưa đọc, thống kê đọc, việc cần làm, ghi chú.
   Dữ liệu lưu cục bộ trong trình duyệt; không gọi API bên ngoài. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const KEY = 'dailyhub_overview', NEWS_STATUS = 'dailyhub_news_status', STATS = 'dailyhub_read_stats';
  const read = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? d; } catch { return d; } };
  const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* đầy hoặc bị chặn */ } };
  const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  const str = (v, n) => (typeof v === 'string' ? v.slice(0, n) : '');
  const dayKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  function normalize(v) {
    const o = v && typeof v === 'object' ? v : {};
    return {
      todos: (Array.isArray(o.todos) ? o.todos : [])
        .filter(t => t && typeof t.text === 'string' && t.text.trim())
        .slice(0, 200).map(t => ({ id: str(t.id, 40) || uid(), text: str(t.text, 200), done: !!t.done })),
      notes: (Array.isArray(o.notes) ? o.notes : [])
        .filter(n => n && typeof n.body === 'string')
        .slice(0, 200).map(n => ({ id: str(n.id, 40) || uid(), title: str(n.title, 120) || 'Không tiêu đề',
          body: str(n.body, 5000), updated: Number(n.updated) || Date.now() })),
      quick: str(o.quick, 5000)
    };
  }
  let data = normalize(read(KEY, null));
  let saveTimer;
  const saveSoon = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => write(KEY, data), 300); };
  const saveNow = () => { clearTimeout(saveTimer); write(KEY, data); };

  /* ----- Điều hướng giữa Tin tức và Tổng quan ----- */
  function setView(view) {
    document.body.dataset.view = view;
    document.body.classList.toggle('view-overview', view === 'overview');  // giữ tương thích
    document.querySelectorAll('.view-btn').forEach(b => {
      const on = b.dataset.view === view;
      b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
    });
    $('overview-view').hidden = view !== 'overview';
    $('library-view').hidden = view !== 'library';
    if (view !== 'news') {
      $('feed-view').hidden = true; $('spider-view').hidden = true;
    }
    if (view === 'overview') renderAll();
    if (view === 'news') {
      // Quay lại đúng kênh đang chọn; script.js sẽ tự hiển thị lại (dùng cache nếu có).
      document.querySelector('#channel-tabs .channel-btn.active')?.click();
    }
  }

  /* ----- Đồng hồ ----- */
  function renderClock() {
    const now = new Date();
    $('ov-date').textContent = now.toLocaleDateString('vi-VN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    $('ov-time').textContent = now.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
  }

  /* ----- Tin mới và thống kê đọc ----- */
  function renderNews() {
    const s = read(NEWS_STATUS, null);
    if (s && typeof s.unread === 'number') {
      $('ov-unread').textContent = s.unread;
      $('ov-news-sub').textContent = `${s.title || 'Tin tức'} · ${s.total} tin · cập nhật ${new Date(s.at).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })}`;
    } else {
      $('ov-unread').textContent = '–';
      $('ov-news-sub').textContent = 'Mở tab Tin tức để cập nhật số liệu.';
    }
  }
  function renderReads() {
    const stats = read(STATS, {}), today = new Date();
    let week = 0;
    for (let i = 0; i < 7; i++) { const d = new Date(); d.setDate(d.getDate() - i); week += (stats && stats[dayKey(d)]) || 0; }
    $('ov-read').textContent = (stats && stats[dayKey(today)]) || 0;
    $('ov-read-sub').textContent = `${week} tin trong 7 ngày`;
  }

  /* ----- Việc cần làm ----- */
  function renderTodoCount() {
    const done = data.todos.filter(t => t.done).length;
    $('ov-todo-count').textContent = data.todos.length ? `${done}/${data.todos.length} đã xong` : 'Chưa có việc nào.';
  }
  function renderTodos() {
    const list = $('ov-todos'); list.replaceChildren();
    data.todos.forEach(t => {
      const li = document.createElement('li'); li.classList.toggle('done', t.done);
      const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = t.done;
      cb.setAttribute('aria-label', 'Đánh dấu hoàn thành: ' + t.text);
      cb.onchange = () => { t.done = cb.checked; li.classList.toggle('done', t.done); renderTodoCount(); saveNow(); };
      const span = document.createElement('span'); span.textContent = t.text;
      const del = document.createElement('button'); del.type = 'button'; del.className = 'ov-del'; del.textContent = 'Xóa';
      del.setAttribute('aria-label', 'Xóa việc: ' + t.text);
      del.onclick = () => { data.todos = data.todos.filter(x => x !== t); saveNow(); renderTodos(); };
      li.append(cb, span, del); list.appendChild(li);
    });
    renderTodoCount();
  }
  $('ov-todo-form').onsubmit = e => {
    e.preventDefault();
    const input = $('ov-todo-input'), text = input.value.trim().slice(0, 200);
    if (!text || data.todos.length >= 200) return;
    data.todos.unshift({ id: uid(), text, done: false });
    input.value = ''; saveNow(); renderTodos();
  };

  /* ----- Ghi chú nhanh ----- */
  $('ov-quick').oninput = e => {
    data.quick = e.target.value.slice(0, 5000);
    $('ov-quick-status').textContent = 'Đang lưu…';
    saveSoon();
    setTimeout(() => { $('ov-quick-status').textContent = 'Đã lưu'; }, 320);
  };

  /* ----- Ghi chú ----- */
  function renderNotes() {
    const box = $('ov-notes'); box.replaceChildren();
    if (!data.notes.length) {
      const p = document.createElement('p'); p.className = 'ov-muted';
      p.textContent = 'Chưa có ghi chú. Bấm "+ Ghi chú mới" để tạo.'; box.appendChild(p); return;
    }
    data.notes.forEach(n => {
      const card = document.createElement('div'); card.className = 'ov-note';
      const title = document.createElement('input'); title.type = 'text'; title.maxLength = 120; title.value = n.title;
      title.setAttribute('aria-label', 'Tiêu đề ghi chú');
      const body = document.createElement('textarea'); body.rows = 4; body.value = n.body;
      body.setAttribute('aria-label', 'Nội dung ghi chú');
      const meta = document.createElement('span'); meta.className = 'ov-muted';
      const stamp = () => { meta.textContent = 'Sửa lúc ' + new Date(n.updated).toLocaleString('vi-VN'); };
      stamp();
      const touch = () => { n.updated = Date.now(); stamp(); saveSoon(); };
      title.oninput = () => { n.title = title.value.slice(0, 120) || 'Không tiêu đề'; touch(); };
      body.oninput = () => { n.body = body.value.slice(0, 5000); touch(); };
      const del = document.createElement('button'); del.type = 'button'; del.className = 'ov-del'; del.textContent = 'Xóa ghi chú';
      del.onclick = () => {
        if (!confirm('Xóa ghi chú này?')) return;
        data.notes = data.notes.filter(x => x !== n); saveNow(); renderNotes();
      };
      card.append(title, body, meta, del); box.appendChild(card);
    });
  }
  $('ov-new-note').onclick = () => {
    if (data.notes.length >= 200) return;
    data.notes.unshift({ id: uid(), title: 'Ghi chú mới', body: '', updated: Date.now() });
    saveNow(); renderNotes();
    $('ov-notes').querySelector('input')?.select();
  };

  function renderAll() {
    renderClock(); renderNews(); renderReads(); renderTodos();
    $('ov-quick').value = data.quick; renderNotes();
  }

  /* ----- Khởi tạo ----- */
  document.querySelectorAll('.view-btn').forEach(b => { b.onclick = () => setView(b.dataset.view); });
  $('ov-open-news').onclick = () => setView('news');
  setInterval(renderClock, 30000);
  document.addEventListener('dailyhub:imported', () => { data = normalize(read(KEY, null)); if (document.body.classList.contains('view-overview')) renderAll(); });
  window.addEventListener('pagehide', saveNow);
})();
