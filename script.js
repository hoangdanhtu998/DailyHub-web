'use strict';
const $ = id => document.getElementById(id);
const PAGE = 12, REFRESH_MS = 5 * 60 * 1000;
const FEED_CACHE_MS = 4 * 60 * 1000, FEED_CONCURRENCY = 8;
const K = { SEEN: 'dailyhub_seen_ids', SAVED: 'dailyhub_saved_items', HIDE: 'dailyhub_hide_seen', THEME: 'dailyhub_theme', WATCH: 'dailyhub_watch_words', COMPACT: 'dailyhub_compact', STATS: 'dailyhub_read_stats' };
function safeUrl(u) {
  return typeof u === 'string' && /^https?:\/\//i.test(u) ? u.replace(/^http:/i, 'https:') : null;
}
function imageUrl(value, base) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try { return safeUrl(new URL(value.trim(), base).href); } catch { return null; }
}
function srcsetImage(value, base) {
  if (typeof value !== 'string') return null;
  const candidates = value.split(',').map(part => {
    const [url, size = ''] = part.trim().split(/\s+/, 2);
    const amount = Number.parseFloat(size);
    return { url, score: Number.isFinite(amount) ? amount : 0 };
  }).filter(item => item.url).sort((a, b) => b.score - a.score);
  return candidates.map(item => imageUrl(item.url, base)).find(Boolean) || null;
}
function extractRssImage(item, html, articleUrl, feedUrl) {
  const candidates = [];
  const add = (value, base = articleUrl) => {
    if (typeof value === 'string') candidates.push(imageUrl(value, base));
    else if (Array.isArray(value)) value.forEach(entry => add(entry, base));
    else if (value && typeof value === 'object') {
      for (const key of ['url', 'href', 'src', 'link']) if (value[key]) add(value[key], base);
    }
  };
  for (const key of ['thumbnail', 'image', 'enclosure', 'media_thumbnail', 'media_content', 'media:thumbnail', 'media:content']) add(item[key]);
  const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
  for (const meta of doc.querySelectorAll('meta[property], meta[name]')) {
    if (/^(og:image|twitter:image)$/i.test(meta.getAttribute('property') || meta.getAttribute('name') || '')) add(meta.getAttribute('content'));
  }
  for (const link of doc.querySelectorAll('link[rel="image_src"][href]')) add(link.getAttribute('href'));
  for (const media of doc.querySelectorAll('img, source, video')) {
    for (const key of ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-original-src', 'data-url', 'poster']) add(media.getAttribute(key));
    for (const key of ['srcset', 'data-srcset']) {
      const best = srcsetImage(media.getAttribute(key), articleUrl);
      if (best) candidates.push(best);
    }
  }
  const absolute = candidates.find(Boolean);
  if (absolute) return absolute;
  for (const key of ['thumbnail', 'image', 'enclosure', 'media_thumbnail', 'media_content', 'media:thumbnail', 'media:content']) {
    const value = item[key];
    const entries = Array.isArray(value) ? value : [value];
    for (const entry of entries) {
      const url = typeof entry === 'string' ? entry : entry && (entry.url || entry.href || entry.src || entry.link);
      const resolved = imageUrl(url, feedUrl);
      if (resolved) return resolved;
    }
  }
  return null;
}

const CHANNELS = {
  all: { title: 'Tổng hợp đầy đủ · Việt Nam, AI, lập trình & GitHub' },
  ai: { title: 'Trí tuệ nhân tạo (AI)', sources: [['TechCrunch AI', 'https://techcrunch.com/category/artificial-intelligence/feed/'], ['The Verge', 'https://www.theverge.com/rss/index.xml']] },
  dev: { title: 'Lập trình viên & cộng đồng', sources: [['Dev.to', 'https://dev.to/feed'], ['Hacker News', 'https://news.ycombinator.com/rss']] },
  github: { title: 'Repo AI mới nổi (tạo trong 7 ngày, nhiều sao nhất)' },
  vn_all: { title: 'Tin Việt Nam · đầy đủ các chủ đề' },
  vn_sport: { title: 'Thể thao Việt Nam' },
  vn_tech: { title: 'Công nghệ Việt Nam' },
  vn_business: { title: 'Kinh doanh Việt Nam' },
  vn_entertainment: { title: 'Giải trí Việt Nam' },
  deals: { title: 'Deal công khai · giảm từ 20%' },
  saved: { title: 'Bài đã lưu' }
};
const EMPTY = {
  saved: 'Bạn chưa lưu bài nào. Bấm ☆ trên một tin để đọc sau.',
  deals: 'Chưa tìm thấy deal công khai có mức giảm từ 20% trong các feed đang hoạt động. Bấm “Tìm bài viết Facebook” hoặc “Tìm nhóm săn deal” để xem kết quả Facebook (cần đăng nhập).'
};
const VN_CATEGORIES = {
  vn_sport: ['Thể thao', 'the-thao'], vn_tech: ['Công nghệ', 'cong-nghe'],
  vn_business: ['Kinh doanh', 'kinh-doanh'], vn_entertainment: ['Giải trí', 'giai-tri']
};
const VN_SOURCES = [
  { name: 'VnExpress', base: 'https://vnexpress.net/rss/', slugs: { '': 'tin-moi-nhat', 'the-thao': 'the-thao', 'cong-nghe': 'so-hoa', 'kinh-doanh': 'kinh-doanh', 'giai-tri': 'giai-tri' } },
  { name: 'Tuổi Trẻ', base: 'https://tuoitre.vn/rss/', slugs: { '': 'tin-moi-nhat', 'the-thao': 'the-thao', 'cong-nghe': 'nhip-song-so', 'kinh-doanh': 'kinh-doanh', 'giai-tri': 'giai-tri' } },
  { name: 'Thanh Niên', base: 'https://thanhnien.vn/rss/', slugs: { '': 'home', 'the-thao': 'the-thao', 'cong-nghe': 'cong-nghe', 'kinh-doanh': 'kinh-te', 'giai-tri': 'giai-tri' } },
  { name: 'Dân Trí', base: 'https://dantri.com.vn/rss/', slugs: { '': 'home', 'the-thao': 'the-thao', 'cong-nghe': 'suc-manh-so', 'kinh-doanh': 'kinh-doanh', 'giai-tri': 'giai-tri' } },
  { name: 'VietnamNet', base: 'https://vietnamnet.vn/rss/', slugs: { '': 'tin-moi-nhat', 'the-thao': 'the-thao', 'cong-nghe': 'cong-nghe', 'kinh-doanh': 'kinh-doanh', 'giai-tri': 'giai-tri' } }
];
const feedCache = new Map();
const aiCache = new Map();
const aiDigestCache = new Map();
async function aiEnrichItems(items) {
  const cacheId = item => `${item.id}|${watchWords.join('\u001f')}`;
  const pending = items.filter(item => !aiCache.has(cacheId(item)));
  for (let i = 0; i < pending.length; i += 30) {
    const batch = pending.slice(i, i + 30);
    const res = await fetch('/api/hub/ai/enrich-news', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DailyHub': '1' },
      body: JSON.stringify({ items: batch.map(({ id, title, summary, source }) => ({ id, title, summary, source })), watch_words: watchWords }), signal: AbortSignal.timeout(95000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `AI không xử lý được tin (HTTP ${res.status})`);
    for (const result of data.items || []) aiCache.set(`${result.id}|${watchWords.join('\u001f')}`, result);
  }
  return items.map(item => {
    const ai = aiCache.get(cacheId(item));
    if (!ai) throw new Error('AI chưa phân tích xong một số tin. Hãy thử tải lại.');
    return { ...item, originalSummary: item.summary, summary: ai.summary_vi || item.summary, aiCategory: ai.category,
      aiTags: ai.tags, aiWatchMatches: ai.watch_matches, relevance: ai.relevance, isDeal: ai.is_deal, discount: ai.discount_percent,
      prices: { current: ai.current_price, old: ai.old_price } };
  });
}
const IMAGE_THEMES = [
  { match: /ai|artificial intelligence|machine learning|llm|robot|openai|gemini|claude|trí tuệ nhân tạo|trí tuệ|mô hình ngôn ngữ/i, label: 'TRÍ TUỆ NHÂN TẠO', start: '#4f46e5', end: '#a855f7', icon: '✦' },
  { match: /github|mã nguồn|source code|repository|repo|developer|lập trình|phần mềm|software|programming|linux/i, label: 'CÔNG NGHỆ & LẬP TRÌNH', start: '#0284c7', end: '#14b8a6', icon: '</>' },
  { match: /thể thao|bóng đá|football|soccer|world cup|v.league|olympic|tennis|cầu thủ/i, label: 'THỂ THAO', start: '#ea580c', end: '#eab308', icon: '⚽' },
  { match: /kinh doanh|kinh tế|doanh nghiệp|thị trường|tài chính|chứng khoán|business|economy|startup/i, label: 'KINH DOANH', start: '#059669', end: '#0ea5e9', icon: '↗' },
  { match: /giải trí|âm nhạc|phim|ca sĩ|showbiz|entertainment|movie|music|nghệ sĩ/i, label: 'GIẢI TRÍ', start: '#db2777', end: '#8b5cf6', icon: '♫' },
  { match: /sức khỏe|y tế|bệnh viện|vaccine|health|medical|medicine/i, label: 'SỨC KHỎE', start: '#0891b2', end: '#22c55e', icon: '+' },
  { match: /giáo dục|học sinh|sinh viên|đại học|education|school|university/i, label: 'GIÁO DỤC', start: '#2563eb', end: '#06b6d4', icon: '▤' }
];
const DEFAULT_IMAGE_THEME = { label: 'TIN TỨC MỚI', start: '#334155', end: '#64748b', icon: '◈' };
const themeFor = (item = {}) => IMAGE_THEMES.find(t => t.match.test(`${item.title || ''} ${item.summary || ''} ${(item.categories || []).join(' ')}`)) || DEFAULT_IMAGE_THEME;
const fallbackImage = (item = {}) => {
  const t = themeFor(item);
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[c]);
  const title = esc(shortExcerpt(item.title || t.label, 64));
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${t.start}"/><stop offset="1" stop-color="${t.end}"/></linearGradient><pattern id="p" width="36" height="36" patternUnits="userSpaceOnUse"><path d="M36 0H0V36" fill="none" stroke="#fff" stroke-opacity=".08"/></pattern></defs><rect width="800" height="450" fill="url(#g)"/><rect width="800" height="450" fill="url(#p)"/><circle cx="690" cy="110" r="150" fill="#fff" opacity=".09"/><circle cx="720" cy="95" r="82" fill="none" stroke="#fff" stroke-opacity=".22" stroke-width="2"/><text x="52" y="120" font-size="68" fill="#fff" font-family="Arial,sans-serif" font-weight="700">${esc(t.icon)}</text><text x="56" y="315" font-size="19" fill="#fff" fill-opacity=".78" font-family="Arial,sans-serif" letter-spacing="3">${esc(t.label)}</text><text x="56" y="365" font-size="25" fill="#fff" font-family="Arial,sans-serif" font-weight="600">${title}</text></svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
};

/* ===== Bộ nhớ: đọc một lần, giữ trong RAM ===== */
const read = (k, d) => { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? d; } catch { return d; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* đầy hoặc bị chặn */ } };
const readSeen = read(K.SEEN, []), readSaved = read(K.SAVED, []);
let seen = new Set(Array.isArray(readSeen) ? readSeen.filter(x => typeof x === 'string').slice(-800) : []);
let saved = Array.isArray(readSaved) ? readSaved.filter(x => x && typeof x.id === 'string' && typeof x.title === 'string' && safeUrl(x.link)) : [];
let watchWords = sanitizeWords(read(K.WATCH, []));
let readStats = sanitizeStats(read(K.STATS, {}));
const st = { ch: 'all', feedChannel: 'all', q: '', aiSearch: null, aiSearchSeq: 0, page: 1, items: [], pending: null, hide: read(K.HIDE, false), cache: new Map(), requestId: 0 };
const els = { grid: $('news-grid'), title: $('current-channel-title'), counter: $('unread-counter'), status: $('status-message'), more: $('load-more-btn'), banner: $('new-news-banner'), count: $('new-count'), markAll: $('mark-all-read'), aiDigest: $('ai-digest') };
const dealTools = $('deals-tools');

/* ===== Dữ liệu ===== */
const stripHtml = h => {
  let text = String(h || '');
  for (let i = 0; i < 6; i++) {
    const decoded = new DOMParser().parseFromString(text, 'text/html').body.textContent.trim();
    if (decoded === text) break;
    text = decoded;
  }
  return text.replace(/&(?:amp;)*apos;/gi, "'").replace(/&(?:amp;)*#39;/gi, "'");
};

async function fetchRSS(feed, name, category = '') {
  const cacheKey = `${feed}|${name}`;
  const cached = feedCache.get(cacheKey);
  if (cached && Date.now() - cached.at < FEED_CACHE_MS) return cached.items;
  const res = await fetch('https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(feed), { signal: AbortSignal.timeout(12000) });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  if (data.status !== 'ok' || !Array.isArray(data.items)) return [];
  const result = data.items.map(it => {
    const link = safeUrl(it.link), t = Date.parse(String(it.pubDate).replace(' ', 'T') + 'Z');
    const html = `${it.content || ''} ${it.description || ''}`;
    const time = Number.isNaN(t) ? 0 : t;                      // thời gian lỗi = 0, không giả làm "vừa đăng"
    const excerpt = stripHtml(it.description || it.content || '').slice(0, 1600);
    const summary = /^(comments?|discuss)$/i.test(excerpt) ? '' : excerpt;
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const productLink = [...doc.querySelectorAll('a[href]')].map(a => safeUrl(a.href)).find(u => u && /(?:shopee|lazada|tiki|tiktok|cellphones|thegioididong|dienmayxanh|sendo)\./i.test(u));
    return { id: link, title: stripHtml(it.title), summary, link, source: name, sources: [name], categories: category ? [category] : [], time,
      buyLink: productLink || link,
      image: extractRssImage(it, html, link, feed),
      badge: time && Date.now() - time < 3600000 ? 'MỚI' : null };
  }).filter(i => i.title && i.link);
  feedCache.set(cacheKey, { at: Date.now(), items: result });
  return result;
}

async function fetchGitHub() {
  const since = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);
  const res = await fetch(`https://api.github.com/search/repositories?q=created:>${since}+topic:ai+topic:llm&sort=stars&order=desc&per_page=20`, { signal: AbortSignal.timeout(12000) });
  if (!res.ok) throw new Error(res.status === 403 ? 'GitHub giới hạn lượt gọi, thử lại sau 1 phút' : 'GitHub lỗi ' + res.status);
  const data = await res.json();
  return (data.items || []).map(r => ({ id: 'gh_' + r.id, title: r.full_name, summary: r.description || 'Kho mã nguồn GitHub mới nổi về AI/LLM.', link: r.html_url,
    source: 'GitHub', categories: ['GitHub AI'], time: Date.parse(r.updated_at) || 0, image: r.owner.avatar_url || null, stars: r.stargazers_count, badge: null }));
}

async function fetchDeals() {
  const sources = [
    ['Mua Gì Giờ?', 'https://mgg.cx/feed.xml'],
    ['Deal1K', 'https://deal1k.vn/rss.xml']
  ];
  const results = await runLimited(sources.map(([name, url]) => () => fetchRSS(url, name, 'Deal')));
  const active = results.filter(x => x.status === 'fulfilled');
  const rows = active.flatMap(x => x.value || []);
  const analyzed = await aiEnrichItems(rows);
  const deals = cluster(analyzed
    .filter(item => item.isDeal && item.discount >= 20)
    .sort((a, b) => b.discount - a.discount || b.time - a.time));
  deals.feedStats = { loaded: active.length, total: sources.length };
  return deals;
}

// Gộp tin trùng giữa nguồn RSS: tiêu đề chung ≥70% từ khóa => một thẻ + số nguồn
const toks = t => new Set(t.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/).filter(w => w.length > 2));
function cluster(list) {
  const out = [], ids = new Set();
  for (const it of list) {
    if (ids.has(it.id)) continue; ids.add(it.id);
    const tk = toks(it.title);
    const hit = tk.size >= 4 && out.find(o => {
      if (o._tk.size < 4) return false;
      let n = 0; tk.forEach(w => o._tk.has(w) && n++);
      return n / Math.min(o._tk.size, tk.size) >= 0.7 && o.source !== it.source;
    });
    if (hit) {
      (hit.also ||= new Set()).add(it.source);
      hit.sources = [...new Set([...(hit.sources || [hit.source]), it.source])];
      hit.categories = [...new Set([...(hit.categories || []), ...(it.categories || [])])];
      if (!hit.summary && it.summary) hit.summary = it.summary;
      if (it.time > hit.time) hit.time = it.time;
    } else { it._tk = tk; out.push(it); }
  }
  return out;
}

async function runLimited(tasks) {
  const results = new Array(tasks.length); let next = 0;
  await Promise.all(Array.from({ length: Math.min(FEED_CONCURRENCY, tasks.length) }, async () => {
    while (next < tasks.length) { const i = next++; try { results[i] = { status: 'fulfilled', value: await tasks[i]() }; } catch (reason) { results[i] = { status: 'rejected', reason }; } }
  }));
  return results;
}

function vietnamTasks(channel) {
  const categoryKeys = channel === 'vn_all' || channel === 'all' ? ['', ...Object.values(VN_CATEGORIES).map(x => x[1])] : [VN_CATEGORIES[channel]?.[1]];
  return VN_SOURCES.flatMap(source => categoryKeys.map(key => {
    const slug = source.slugs[key];
    return () => fetchRSS(source.base + slug + '.rss', source.name, key ? (Object.values(VN_CATEGORIES).find(x => x[1] === key)?.[0] || '') : 'Tin Việt Nam');
  }));
}

async function fetchChannel(ch) {
  if (ch === 'spider') return st.items;
  if (!CHANNELS[ch]) throw new Error('Kênh không hợp lệ');
  if (ch === 'saved') return aiEnrichItems(saved);
  if (ch === 'deals') {
    return fetchDeals();
  }
  const tasks = ch === 'all' ? [
    ...CHANNELS.ai.sources.map(([n, u]) => () => fetchRSS(u, n, 'AI')),
    ...CHANNELS.dev.sources.map(([n, u]) => () => fetchRSS(u, n, 'Devs & Tech')),
    ...vietnamTasks('all'), () => fetchGitHub()
  ] : VN_CATEGORIES[ch] || ch === 'vn_all' ? vietnamTasks(ch)
    : ch === 'github' ? [() => fetchGitHub()]
    : (CHANNELS[ch].sources || []).map(([n, u]) => () => fetchRSS(u, n, ch === 'ai' ? 'AI' : 'Devs & Tech'));
  const results = await runLimited(tasks);
  const ok = results.filter(r => r.status === 'fulfilled');
  if (!ok.length) throw new Error('Không nguồn nào phản hồi');
  let items = cluster(ok.flatMap(r => Array.isArray(r.value) ? r.value : []).sort((a, b) => b.time - a.time));
  // Một số nguồn lỗi và không có tin nào: báo lỗi thay vì hiển thị danh sách trống như thể không có gì mới.
  if (!items.length && ok.length < tasks.length) throw new Error(`chỉ ${ok.length}/${tasks.length} nguồn phản hồi và không có tin nào`);
  if (ch === 'all') items = balanceAllTopics(items);
  if (ch !== 'saved') items = (await aiEnrichItems(items)).sort((a, b) => b.relevance - a.relevance || b.time - a.time);
  items.feedStats = { loaded: ok.length, total: tasks.length };
  return items;
}

function balanceAllTopics(items) {
  const buckets = [
    items.filter(x => x.categories?.includes('AI')),
    items.filter(x => x.categories?.includes('Devs & Tech')),
    items.filter(x => x.categories?.includes('GitHub AI'))
  ];
  const chosen = [], used = new Set();
  for (const bucket of buckets) for (const item of bucket.slice(0, 2)) {
    if (!used.has(item.id)) { chosen.push(item); used.add(item.id); }
  }
  for (const item of items) if (!used.has(item.id)) { chosen.push(item); used.add(item.id); }
  return chosen;
}

function shortExcerpt(text, limit = 360) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  if (clean.length <= limit) return clean;
  const slice = clean.slice(0, limit), boundary = slice.lastIndexOf(' ');
  return slice.slice(0, boundary > limit * 0.75 ? boundary : limit).trimEnd() + '…';
}

/* ===== Giao diện ===== */
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const plain = ({ id, title, summary, link, buyLink, prices, discount, source, time, image, stars, categories, aiCategory, aiTags, aiWatchMatches, relevance }) => ({ id, title, summary, link, buyLink, prices, discount, source, time, image, stars, categories, aiCategory, aiTags, aiWatchMatches, relevance });
const EN_WORDS = /\b(the|and|for|with|from|after|before|this|that|says|said|new|how|why|what|when|will|has|have|its|their|you|your|into|over|under|about|more|than|could|would|should|ai|model|release|launch|update)\b/gi;
function needsVietnamese(item) {
  const text = `${item.title || ''} ${item.summary || ''}`;
  if (/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u0400-\u04ff]/.test(text)) return true;
  if (/[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]/i.test(text)) return false;
  return (text.match(EN_WORDS) || []).length >= 2;
}
const translationCache = new Map();
async function translateToVietnamese(...texts) {
  const values = texts.map(text => String(text || '').trim().slice(0, 1200));
  if (values.every(value => !value)) return values.map(() => '');
  if (values.every(value => !value || translationCache.has(value))) return values.map(value => value ? translationCache.get(value) : '');
  const res = await fetch('/api/hub/ai/translate', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DailyHub': '1' },
    body: JSON.stringify({ texts: values.filter(Boolean) }), signal: AbortSignal.timeout(95000) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Dịch lỗi HTTP ${res.status}`);
  let index = 0;
  values.forEach(value => { if (value) translationCache.set(value, data.translations?.[index++] || ''); });
  return values.map(value => value ? translationCache.get(value) : '');
}
function timeAgo(t) {
  if (!t) return '';
  const m = Math.floor((Date.now() - t) / 60000);
  return m < 1 ? 'Vừa xong' : m < 60 ? m + ' phút trước' : m < 1440 ? Math.floor(m / 60) + ' giờ trước' : Math.floor(m / 1440) + ' ngày trước';
}

function card(it, featured) {
  const root = el('article', 'card' + (featured ? ' card-featured' : '') + (seen.has(it.id) ? ' is-seen' : ''));
  const a = el('a', 'card-link'); a.href = it.link; a.target = '_blank'; a.rel = 'noopener noreferrer';
  const mark = () => {
    const fresh = !seen.has(it.id);
    seen.add(it.id); seen = new Set([...seen].slice(-800)); write(K.SEEN, [...seen]);
    if (fresh) bumpStats();
    root.classList.add('is-seen');
    if (st.hide && st.ch !== 'saved') { root.remove(); counter(); }
  };
  a.addEventListener('click', mark); a.addEventListener('auxclick', mark);   // cả chuột giữa

  const wrap = el('div', 'card-img-wrap'), img = el('img', 'card-img');
  img.loading = 'lazy'; img.alt = it.title ? `Ảnh minh họa: ${it.title}` : 'Ảnh minh họa tin tức';
  img.src = safeUrl(it.image) || fallbackImage(it);
  img.onerror = () => { img.onerror = null; img.src = fallbackImage(it); };
  wrap.appendChild(img);

  const meta = el('div', 'card-meta');
  if (it.discount) meta.appendChild(el('span', 'tag-discount', `−${it.discount}%`));
  if (it.badge) meta.appendChild(el('span', 'badge-new', it.badge));
  meta.appendChild(el('span', 'meta-source', it.source));
  if (it.also && it.also.size) meta.appendChild(el('span', 'tag-multi', '+' + it.also.size + ' nguồn'));
  [...new Set([it.aiCategory, ...(it.aiTags || []), ...(it.categories || []).filter(x => x !== 'Tin Việt Nam')].filter(Boolean))].slice(0, 4).forEach(x => meta.appendChild(el('span', 'tag-category', x)));
  watchMatches(it).slice(0, 2).forEach(w => meta.appendChild(el('span', 'tag-watch', '◉ ' + w)));
  if (it.stars) meta.appendChild(el('span', 'tag-stars', '★ ' + Number(it.stars).toLocaleString()));
  meta.appendChild(el('span', '', timeAgo(it.time)));
  const body = el('div', 'card-body');
  body.appendChild(el('h3', 'card-title', it.title));
  if (it.summary) body.appendChild(el('p', 'card-summary', shortExcerpt(it.summary)));
  if (st.ch === 'deals') {
    const price = it.prices || {};
    if (price.current || price.old) body.appendChild(el('p', 'deal-price', `Giá hiện tại: ${price.current || 'chưa có'}${price.old ? ` · Giá cũ: ${price.old}` : ''}`));
    // Link mua không đặt lồng trong thẻ <a> bài viết (HTML không cho phép); dùng nút mở tab mới,
    // và không đánh dấu tin đã đọc khi chỉ mở trang thương mại.
    if (it.buyLink && it.buyLink !== it.link) {
      const buyBtn = el('button', 'deal-buy-link', 'Mở link mua'); buyBtn.type = 'button';
      buyBtn.onclick = e => { e.preventDefault(); e.stopPropagation(); window.open(it.buyLink, '_blank', 'noopener,noreferrer'); };
      body.appendChild(buyBtn);
    } else body.appendChild(el('span', 'deal-buy-link', 'Xem bài deal →'));
  }
  body.appendChild(meta);
  a.append(wrap, body);

  if (needsVietnamese(it)) {
    const translation = el('div', 'translation-box');
    const button = el('button', 'translate-btn', 'Dịch sang tiếng Việt'); button.type = 'button';
    const result = el('div', 'translated-text hidden'); result.setAttribute('aria-live', 'polite');
    button.onclick = async e => {
      e.preventDefault(); e.stopPropagation(); button.disabled = true; button.textContent = 'Đang dịch…';
      try {
        const [title, summary] = await translateToVietnamese(it.title, it.summary || '');
        result.replaceChildren(el('strong', '', 'Bản dịch tiếng Việt (tự động)'), el('p', '', title), ...(summary ? [el('p', '', summary)] : []));
        result.classList.remove('hidden'); button.textContent = 'Dịch lại';
      } catch (err) { button.textContent = 'Dịch không thành công · thử lại'; }
      finally { button.disabled = false; }
    };
    translation.append(button, result);
    root.appendChild(translation);
  }

  const b = el('button', 'bookmark-btn'); b.type = 'button';
  const paint = () => { const on = saved.some(s => s.id === it.id); b.textContent = on ? '★' : '☆'; b.setAttribute('aria-pressed', on); b.setAttribute('aria-label', on ? 'Bỏ lưu' : 'Lưu đọc sau'); };
  b.onclick = () => {
    const i = saved.findIndex(s => s.id === it.id);
    if (i > -1) saved.splice(i, 1); else saved.unshift(plain(it));
    write(K.SAVED, saved); paint();
    if (st.ch === 'saved') { st.items = saved; render(); }
  };
  paint();
  root.append(a, b);
  return root;
}

function counter() {
  const feeds = st.items.feedStats;
  els.counter.textContent = st.ch === 'deals' ? '' : `${st.items.filter(i => !seen.has(i.id)).length} chưa đọc / ${st.items.length} tổng${feeds ? ` · ${feeds.loaded}/${feeds.total} luồng hoạt động` : ''}`;
  // Chia sẻ số liệu với khu vực Tổng quan (overview.js).
  if (!['deals', 'spider'].includes(st.ch) && Array.isArray(st.items)) {
    write('dailyhub_news_status', { unread: st.items.filter(i => !seen.has(i.id)).length, total: st.items.length, title: CHANNELS[st.ch]?.title || '', at: Date.now() });
  }
}

function showSeenButton() {
  const btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = 'Hiện tin đã xem';
  btn.onclick = () => { st.hide = false; write(K.HIDE, false); hideBtn.setAttribute('aria-pressed', 'false'); render(); };
  return btn;
}

function renderAiDigest(items) {
  const box = els.aiDigest;
  if (!['all', 'ai'].includes(st.ch) || st.q || !Array.isArray(items) || !items.length) { box.hidden = true; box.replaceChildren(); return; }
  const highlights = [...items].sort((a, b) => (b.relevance || 0) - (a.relevance || 0) || (b.time || 0) - (a.time || 0)).slice(0, 3);
  const channel = st.ch;
  const briefItems = [...items].sort((a, b) => (b.relevance || 0) - (a.relevance || 0) || (b.time || 0) - (a.time || 0)).slice(0, 30);
  const cacheKey = `${channel}|${briefItems.map(item => `${item.id}:${(item.summary || '').slice(0, 100)}`).join('|')}`;
  let brief = aiDigestCache.get(cacheKey);
  if (!brief) {
    if (aiDigestCache.size > 20) aiDigestCache.clear();
    brief = { status: 'loading' }; aiDigestCache.set(cacheKey, brief);
    fetch('/api/hub/ai/summarize-news', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DailyHub': '1' },
      body: JSON.stringify({ items: briefItems.map(item => ({ id: item.id, title: item.title, summary: item.summary, source: item.source,
        category: item.aiCategory, relevance: item.relevance })) }), signal: AbortSignal.timeout(95000) })
      .then(async res => { const result = await res.json().catch(() => ({})); if (!res.ok) throw new Error(result.error || `AI tổng hợp lỗi HTTP ${res.status}`); return result; })
      .then(result => { aiDigestCache.set(cacheKey, { ...result, status: 'ok' }); if (st.ch === channel && st.items === items) renderAiDigest(items); })
      .catch(err => { aiDigestCache.set(cacheKey, { status: 'error', message: err.message }); if (st.ch === channel && st.items === items) renderAiDigest(items); });
  }
  const heading = el('div', 'ai-digest-heading');
  const title = el('div', 'ai-digest-title');
  title.append(el('span', 'ai-digest-kicker', 'DAILYHUB AI BRIEF'), el('h2', '', st.ch === 'ai' ? 'Điểm nóng về AI' : 'Điểm đáng chú ý hôm nay'));
  title.appendChild(el('p', 'ai-digest-subtitle', `AI đã đọc ${items.length} tin${st.items.feedStats ? ` từ ${st.items.feedStats.loaded} luồng tin` : ''}; bản tổng hợp dùng ${briefItems.length} bài nổi bật.`));
  heading.append(title, el('span', 'ai-digest-count', `${items.length} TIN ĐÃ PHÂN TÍCH`));
  const lead = el('div', 'ai-digest-lead');
  if (brief.status === 'ok') {
    lead.append(el('h3', 'ai-digest-headline', brief.headline), el('p', 'ai-digest-summary', brief.summary));
    if (brief.themes?.length) {
      const themes = el('div', 'ai-digest-themes');
      themes.append(...brief.themes.map(theme => el('span', '', theme)));
      lead.appendChild(themes);
    }
  } else {
    lead.append(el('p', brief.status === 'error' ? 'ai-digest-summary ai-digest-error' : 'ai-digest-summary', brief.status === 'error'
      ? `Chưa tạo được bản tổng hợp: ${brief.message}` : 'AI đang đọc các tin để tạo bản tổng hợp ngắn…'));
    if (brief.status === 'error') {
      const retry = el('button', 'ai-digest-retry', 'Thử tổng hợp lại');
      retry.onclick = () => { aiDigestCache.delete(cacheKey); renderAiDigest(items); };
      lead.appendChild(retry);
    }
  }
  const list = el('div', 'ai-digest-highlights');
  highlights.forEach((item, index) => {
    const link = el('a', 'ai-highlight');
    link.href = item.link; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.addEventListener('click', () => { if (!seen.has(item.id)) { seen.add(item.id); seen = new Set([...seen].slice(-800)); write(K.SEEN, [...seen]); bumpStats(); counter(); } });
    link.append(el('span', 'ai-highlight-rank', String(index + 1).padStart(2, '0')));
    const copy = el('div', 'ai-highlight-copy');
    copy.append(el('span', 'ai-highlight-category', item.aiCategory || (st.ch === 'ai' ? 'TRÍ TUỆ NHÂN TẠO' : 'TIN ĐÁNG CHÚ Ý')),
      el('strong', 'ai-highlight-title', item.title), el('span', 'ai-highlight-summary', shortExcerpt(item.summary || '', 220)),
      el('span', 'ai-highlight-source', `${item.source || 'Nguồn tin'} · ${timeAgo(item.time)}`));
    const score = el('span', 'ai-highlight-score', String(item.relevance || 0));
    score.appendChild(el('small', '', 'AI'));
    link.append(copy, score);
    list.appendChild(link);
  });
  box.replaceChildren(heading, lead, list); box.hidden = false;
}

function render() {
  let list = st.items;
  if (st.hide && st.ch !== 'saved') list = list.filter(i => !seen.has(i.id));
  if (st.watchOnly) list = list.filter(i => watchMatches(i).length > 0);
  if (st.q) {
    if (st.aiSearch?.query === st.q && Array.isArray(st.aiSearch.ids)) { const rank = new Map(st.aiSearch.ids.map((id, index) => [id, index])); list = list.filter(i => rank.has(i.id)).sort((a, b) => rank.get(a.id) - rank.get(b.id)); }
    else list = [];
  }
  renderAiDigest(st.q ? [] : st.items);
  const shown = list.slice(0, st.page * PAGE);
  els.grid.replaceChildren(...shown.map((it, k) => {
    const node = card(it, k === 0 && !st.q && st.page === 1);
    node.style.setProperty('--i', Math.min(k, 8));   // so tin xuất hiện lần lượt
    return node;
  }));
  els.grid.setAttribute('aria-busy', 'false');
  els.more.classList.toggle('hidden', list.length <= shown.length);
  if (!list.length && st.hide && st.ch !== 'saved' && st.items.length) {
    // Mọi tin đều đã xem và đang bị ẩn: nói rõ và cho phép hiện lại ngay.
    els.status.replaceChildren(document.createTextNode(`Cả ${st.items.length} tin đều đã xem nên đang bị ẩn. `), showSeenButton());
    els.status.classList.remove('error'); els.status.classList.remove('hidden');
    counter();
    return;
  }
  const msg = list.length ? '' : (st.q && st.aiSearch?.query === st.q && st.aiSearch.loading ? 'AI đang tìm tin phù hợp…'
    : st.q && st.aiSearch?.query === st.q && st.aiSearch.error ? st.aiSearch.error
    : st.ch === 'deals' && st.items.feedStats?.loaded === 0
    ? 'Các RSS deal chưa phản hồi. Thử lại sau, hoặc bấm liên kết Facebook để tìm bài/nhóm săn deal (cần đăng nhập).'
    : (st.watchOnly ? 'Chưa có tin nào khớp từ khóa theo dõi.' : (EMPTY[st.ch] || 'Không có nội dung phù hợp (hoặc bạn đã xem hết).')));
  els.status.textContent = msg; els.status.classList.remove('error'); els.status.classList.toggle('hidden', !msg);
  counter();
}

window.addEventListener('dailyhub:model-changed', () => { aiDigestCache.clear(); if (['all', 'ai'].includes(st.ch)) render(); });

function skeleton() {
  els.aiDigest.hidden = true;
  els.grid.replaceChildren(...Array.from({ length: 6 }, () => el('div', 'skeleton-card')));
  els.grid.setAttribute('aria-busy', 'true'); els.status.classList.add('hidden'); els.more.classList.add('hidden');
}

async function loadChannel(ch, force = false) {
  Object.assign(st, { ch, page: 1, pending: null });
  st.feedChannel = ch;
  const requestId = ++st.requestId;
  els.banner.classList.add('hidden');
  dealTools.classList.toggle('hidden', ch !== 'deals');
  els.markAll.classList.toggle('hidden', ch === 'saved');
  els.title.textContent = CHANNELS[ch].title;
  if (ch === 'spider') { sendSpider(); return; }
  if (!force && ch !== 'deals' && st.cache.has(ch)) { st.items = st.cache.get(ch); return render(); }
  skeleton();
  try {
    const items = await fetchChannel(ch);
    if (requestId !== st.requestId || ch !== st.ch) return;
    st.items = items; if (ch !== 'saved') st.cache.set(ch, items);
    render();
  } catch (e) {
    if (requestId !== st.requestId || ch !== st.ch) return;
    st.items = []; els.grid.replaceChildren(); els.grid.setAttribute('aria-busy', 'false');
    els.aiDigest.hidden = true;
    els.status.textContent = 'Không tải được: ' + e.message + '.';
    els.status.classList.add('error'); els.status.classList.remove('hidden');
  }
}

let lastCheck = 0;
async function checkUpdates() {
  if (document.hidden || ['saved', 'deals', 'spider'].includes(st.ch) || !$('spider-view').hidden || document.body.dataset.view !== 'news') return;
  lastCheck = Date.now();
  const ch = st.ch;
  try {
    const fresh = await fetchChannel(ch);
    if (ch !== st.ch || document.hidden) return;
    const have = new Set(st.items.map(i => i.id)), n = fresh.filter(i => !have.has(i.id)).length;
    if (n) { st.pending = fresh; $('new-count').textContent = n; els.banner.classList.remove('hidden'); }
  } catch { /* lần sau */ }
}

/* ===== Cầu nối sang Prompt Spider: cùng dữ liệu, không gọi RSS lại ===== */
const frame = $('spider-frame'); let frameReady = false;
frame.addEventListener('load', () => { frameReady = true; sendSpider(); });
function sendSpider() {
  $('spider-msg').textContent = frameReady
    ? 'Spider đang chờ request FreeLLMAPI mới; kiểm tra mỗi giây.'
    : 'Đang mở Spider để theo dõi request mới theo thời gian thực.';
}
function showView(ch) {
  const sp = ch === 'spider';
  $('feed-view').hidden = sp; $('spider-view').hidden = !sp;
  $('search-input').parentElement.hidden = sp;
  if (sp) { $('search-input').value = ''; $('clear-search').classList.add('hidden'); st.q = ''; }
  if (!sp) return loadChannel(ch);
  st.ch = ch;
  ++st.requestId;
  st.pending = null;
  els.banner.classList.add('hidden');
  if (!frame.getAttribute('src')) frame.src = 'spider.html'; else sendSpider();   // tải lười: đúng kích thước khi đã hiện
}

/* ===== Sự kiện ===== */
$('channel-tabs').addEventListener('click', e => {
  const b = e.target.closest('.channel-btn'); if (!b) return;
  const isSpider = b.dataset.channel === 'spider';
  document.body.dataset.view = isSpider ? 'spider' : 'news';
  document.querySelectorAll('.view-tabs .view-btn').forEach(tab => {
    const on = tab.dataset.view === document.body.dataset.view;
    tab.classList.toggle('active', on); tab.setAttribute('aria-pressed', String(on));
  });
  document.querySelectorAll('.channel-btn').forEach(x => {
    x.classList.toggle('active', x === b);
    x.setAttribute('aria-pressed', String(x === b));
  });
  st.q = ''; $('search-input').value = ''; $('clear-search').classList.add('hidden');
  clearTimeout(timer);
  showView(b.dataset.channel);
});
$('spider-send').onclick = sendSpider;

const hideBtn = $('toggle-hide-seen'); hideBtn.setAttribute('aria-pressed', st.hide);
hideBtn.onclick = () => { st.hide = !st.hide; write(K.HIDE, st.hide); hideBtn.setAttribute('aria-pressed', st.hide); render(); };

let timer;
async function runAiNewsSearch(query) {
  const seq = ++st.aiSearchSeq;
  st.aiSearch = { query, loading: true }; render();
  try {
    const items = st.items.slice(0, 40).map(({ id, title, summary, aiTags }) => ({ id, title, summary, tags: aiTags }));
    const res = await fetch('/api/hub/ai/search-news', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-DailyHub': '1' },
      body: JSON.stringify({ query, items }), signal: AbortSignal.timeout(95000) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `AI tìm kiếm lỗi HTTP ${res.status}`);
    if (seq !== st.aiSearchSeq || query !== st.q) return;
    st.aiSearch = { query, ids: data.ids || [], loading: false };
  } catch (err) {
    if (seq !== st.aiSearchSeq || query !== st.q) return;
    st.aiSearch = { query, ids: [], loading: false, error: err.message };
  }
  render();
}
$('search-input').addEventListener('input', e => {
  $('clear-search').classList.toggle('hidden', !e.target.value);
  clearTimeout(timer);
  timer = setTimeout(() => { st.q = e.target.value.trim(); st.page = 1; st.aiSearch = null; if (st.q.length >= 2) runAiNewsSearch(st.q); else if (st.q) { st.aiSearch = { query: st.q, error: 'Nhập ít nhất 2 ký tự để AI tìm kiếm.' }; render(); } else render(); }, 550);
});
$('clear-search').onclick = () => { $('search-input').value = ''; $('clear-search').classList.add('hidden'); st.q = ''; st.aiSearchSeq++; st.aiSearch = null; st.page = 1; render(); $('search-input').focus(); };
els.more.onclick = () => { st.page++; render(); };
els.markAll.onclick = () => {
  const list = st.items.filter(i => i && i.id && !seen.has(i.id));
  if (!list.length) return;
  if (!confirm(`Đánh dấu ${list.length} tin là đã đọc?`)) return;
  const before = [...seen];
  list.forEach(i => seen.add(i.id));
  seen = new Set([...seen].slice(-800)); write(K.SEEN, [...seen]);
  st.page = 1; render();
  // Hoàn tác ngay sau thao tác (đề phòng bấm nhầm).
  const undo = document.createElement('button');
  undo.type = 'button'; undo.textContent = 'Hoàn tác';
  undo.onclick = () => { seen = new Set(before); write(K.SEEN, [...seen]); render(); };
  const nodes = [document.createTextNode(`Đã đánh dấu ${list.length} tin. `), undo];
  if (st.hide && !els.grid.children.length) nodes.push(document.createTextNode(' '), showSeenButton());
  els.status.replaceChildren(...nodes);
  els.status.classList.remove('error'); els.status.classList.remove('hidden');
};
$('apply-update').onclick = () => {
  if (!st.pending) return;
  st.items = st.pending; st.cache.set(st.ch, st.pending); st.pending = null; st.page = 1;
  els.banner.classList.add('hidden'); render(); scrollTo({ top: 0 });
};
$('theme-toggle').onclick = () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next; write(K.THEME, next);
  $('theme-toggle').setAttribute('aria-label', next === 'dark' ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối');
};
setInterval(checkUpdates, REFRESH_MS);
// Quay lại tab: tải lại nếu danh sách đang trống (lần tải trước lỗi), hoặc kiểm tra tin mới nếu đã quá chu kỳ.
document.addEventListener('visibilitychange', () => {
  if (document.hidden || document.body.dataset.view !== 'news') return;
  if (st.items.length === 0 && st.ch !== 'spider') return loadChannel(st.ch, true);
  if (Date.now() - lastCheck >= REFRESH_MS) checkUpdates();
});
// Mở bằng file:// thay vì http://127.0.0.1:8766: tin vẫn chạy, nhưng Thư viện và sao lưu cần máy chủ.
if (location.protocol === 'file:') {
  const note = $('env-notice');
  note.textContent = 'Bạn đang mở trực tiếp từ file. Tin tức vẫn chạy, nhưng Thư viện AI, sao lưu và thời tiết cần mở qua http://127.0.0.1:8766 (chạy Run-FreeLLMAPI-Spider.bat).';
  note.classList.remove('hidden');
}


/* ===== Từ khóa theo dõi ===== */
function sanitizeWords(v) {
  return Array.isArray(v) ? [...new Set(v.filter(x => typeof x === 'string').map(x => x.trim().slice(0, 40)).filter(Boolean))].slice(0, 30) : [];
}
function watchMatches(item) {
  if (!watchWords.length) return [];
  return Array.isArray(item.aiWatchMatches) ? item.aiWatchMatches : [];
}
function renderWatch() {
  if (!watchWords.length) st.watchOnly = false;
  const box = $('watch-chips'); box.replaceChildren();
  watchWords.forEach((w, i) => {
    const chip = el('span', 'watch-chip', w);
    const rm = el('button', 'watch-remove', '×'); rm.type = 'button'; rm.setAttribute('aria-label', `Bỏ theo dõi ${w}`);
    rm.onclick = () => { watchWords.splice(i, 1); write(K.WATCH, watchWords); renderWatch(); loadChannel(st.ch, true); };
    chip.appendChild(rm); box.appendChild(chip);
  });
  const only = $('watch-only');
  only.disabled = !watchWords.length;
  only.setAttribute('aria-pressed', String(!!st.watchOnly));
}
$('watch-form').onsubmit = e => {
  e.preventDefault();
  const input = $('watch-input'), w = input.value.trim().slice(0, 40);
  input.value = '';
  if (!w || watchWords.length >= 30 || watchWords.some(x => x.toLowerCase() === w.toLowerCase())) return;
  watchWords.unshift(w); write(K.WATCH, watchWords); renderWatch(); loadChannel(st.ch, true);
};
$('watch-only').onclick = () => { if (!watchWords.length) return; st.watchOnly = !st.watchOnly; renderWatch(); render(); };

/* ===== Thống kê đọc (lưu cục bộ, 90 ngày) ===== */
function sanitizeStats(v) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {};
  return Object.fromEntries(Object.entries(v).filter(([d, n]) => /^\d{4}-\d{2}-\d{2}$/.test(d) && Number.isFinite(n) && n > 0));
}
const dayKey = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
function bumpStats() {
  const k = dayKey(new Date());
  readStats[k] = (readStats[k] || 0) + 1;
  Object.keys(readStats).sort().slice(0, -90).forEach(d => delete readStats[d]);
  write(K.STATS, readStats); renderStats();
}
function renderStats() {
  let week = 0;
  for (let i = 0; i < 7; i++) { const d = new Date(); d.setDate(d.getDate() - i); week += readStats[dayKey(d)] || 0; }
  $('reading-stats').textContent = `Hôm nay: ${readStats[dayKey(new Date())] || 0} tin · 7 ngày: ${week} tin đã đọc`;
}

/* ===== Chế độ gọn ===== */
function applyCompact() {
  document.body.classList.toggle('compact', !!st.compact);
  $('toggle-compact').setAttribute('aria-pressed', String(!!st.compact));
}
$('toggle-compact').onclick = () => { st.compact = !st.compact; write(K.COMPACT, st.compact); applyCompact(); };

/* ===== Phím tắt: "/" mở tìm kiếm, Esc xóa ===== */
document.addEventListener('keydown', e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $('search-input').focus(); }
  else if (e.key === 'Escape' && e.target === $('search-input') && $('search-input').value) $('clear-search').click();
});

/* ===== Sao lưu & khôi phục (JSON) ===== */
function notify(msg, isError) {
  els.status.textContent = msg; els.status.classList.toggle('error', !!isError); els.status.classList.remove('hidden');
}
$('export-data').onclick = () => {
  const payload = { app: 'DailyHub', version: 1, exportedAt: new Date().toISOString(), seen: [...seen], saved, watch: watchWords,
    hide: !!st.hide, compact: !!st.compact, stats: readStats, theme: document.documentElement.dataset.theme, overview: read('dailyhub_overview', null) };
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
  const a = el('a'); a.href = url; a.download = `dailyhub-backup-${dayKey(new Date())}.json`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('import-data').onchange = async e => {
  const file = e.target.files[0]; e.target.value = '';
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) return notify('Tệp quá lớn (tối đa 2 MB).', true);
  try {
    const data = JSON.parse(await file.text());
    if (!data || data.app !== 'DailyHub' || data.version !== 1) throw new Error('định dạng không đúng');
    const incomingSaved = (Array.isArray(data.saved) ? data.saved : []).filter(x => x && typeof x.id === 'string' && typeof x.title === 'string' && safeUrl(x.link));
    const ids = new Set(saved.map(x => x.id));
    saved = [...saved, ...incomingSaved.filter(x => !ids.has(x.id))].slice(0, 500);
    seen = new Set([...seen, ...(Array.isArray(data.seen) ? data.seen.filter(x => typeof x === 'string') : [])].slice(-800));
    watchWords = sanitizeWords([...watchWords, ...(Array.isArray(data.watch) ? data.watch : [])]);
    const incomingStats = sanitizeStats(data.stats);
    for (const [d, n] of Object.entries(incomingStats)) readStats[d] = Math.max(readStats[d] || 0, n);
    if (typeof data.hide === 'boolean') { st.hide = data.hide; write(K.HIDE, st.hide); hideBtn.setAttribute('aria-pressed', st.hide); }
    if (typeof data.compact === 'boolean') { st.compact = data.compact; write(K.COMPACT, st.compact); applyCompact(); }
    if (data.overview && typeof data.overview === 'object') { write('dailyhub_overview', data.overview); document.dispatchEvent(new Event('dailyhub:imported')); }
    write(K.SEEN, [...seen]); write(K.SAVED, saved); write(K.WATCH, watchWords); write(K.STATS, readStats);
    renderWatch(); renderStats(); render();
    notify(`Đã nhập dữ liệu: ${incomingSaved.length} tin đã lưu, ${watchWords.length} từ khóa theo dõi.`);
  } catch (err) {
    notify('Không nhập được tệp: ' + err.message + '.', true);
  }
};

/* ===== PWA: cache vỏ ứng dụng để mở nhanh và dùng khi mất mạng ===== */
if (navigator.serviceWorker) navigator.serviceWorker.register('sw.js').catch(() => {});

/* ===== Khởi động ===== */
$('year').textContent = new Date().getFullYear();
document.documentElement.dataset.theme = read(K.THEME, null) || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
const dark = document.documentElement.dataset.theme === 'dark';
$('theme-toggle').setAttribute('aria-label', dark ? 'Chuyển sang giao diện sáng' : 'Chuyển sang giao diện tối');
st.compact = !!read(K.COMPACT, false);
applyCompact(); renderWatch(); renderStats();
loadChannel('all');
