/* 修正モード。**直したいところを四角で囲む。**

   文字を選ばせる形はやめた。表の枠・空欄・余白など、
   **文字が無いところを指せない**のと、選び直しが要って手数が増える。
   囲むだけなら、どこでも同じ操作で済む。 */
(function () {
  var KEY = 'fix_mode';
  /* 土台に知らせる口が無ければ、自前で出す。**黙って終わらせない。** */
  var say = window.toast || function (m) {
    var t = document.createElement('div');
    t.className = 'notetoast';
    t.textContent = m;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2400);
  };
  var sw = document.getElementById('modeswitch');
  var bar = document.getElementById('notebar');
  var layer = document.getElementById('notelayer');
  var openBtn = document.getElementById('noteopen');
  var panel = document.getElementById('notepanel');
  if (!sw || !panel || !layer) return;

  var body = document.getElementById('notebody');
  var quoteView = document.getElementById('notequoteview');
  var form = document.getElementById('noteform');
  var read = document.getElementById('noteread');
  var title = document.getElementById('notepaneltitle');
  var check = document.getElementById('notecheck');
  var ngbox = document.getElementById('notengbox');
  var hintmsg = document.getElementById('notehintmsg');
  var me = document.getElementById('fixmodejs');
  var canCheck = me && me.dataset.canClose === '1';
  var endpoint = (me && me.dataset.endpoint) || '/notes';

  /* CSRF を使っていない土台でも動くようにする */
  function csrf() {
    var m = document.querySelector('meta[name=csrf-token]');
    return m ? m.content : '';
  }
  var showing = null;
  var pending = null;                       // いま囲んでいる四角
  var notes = [];

  try { notes = JSON.parse(document.getElementById('notedata').textContent) || []; }
  catch (e) { notes = []; }

  function on() { return localStorage.getItem(KEY) === '1'; }

  function docSize() {
    var d = document.documentElement;
    return {w: Math.max(d.scrollWidth, window.innerWidth),
            h: Math.max(d.scrollHeight, window.innerHeight)};
  }

  function paint() {
    var v = on();
    sw.classList.toggle('on', v);
    bar.hidden = !v;
    layer.hidden = !v;
    openBtn.hidden = !v;
    document.body.classList.toggle('fixmode', v);
    if (!v) { panel.hidden = true; }
    else {
      drawAll();
      var waiting = notes.filter(function (n) { return n.state === 'fixed'; }).length;
      if (waiting && hintmsg) {
        hintmsg.innerHTML = '<b>緑の枠が' + waiting +
          'つあります。</b>直したところです。押して、直っているかご覧ください。';
      }
    }
  }

  function setMode(v) {
    try { localStorage.setItem(KEY, v ? '1' : '0'); } catch (e) { /* 使えなくても動く */ }
    paint();
  }

  sw.addEventListener('click', function () { setMode(!on()); });
  document.getElementById('notemodeoff').addEventListener('click', function () {
    setMode(false);
  });

  /* ---- 届いているものを、囲みとして描き戻す ---- */
  /* **拾った文字を手がかりに、いまの位置を探し直す。**
     画面の中身が変われば、割合で持った座標は合わなくなる。
     文字が見つかればそこに、見つからなければ元の座標に、印をつける。 */
  function findByQuote(q) {
    if (!q) return null;
    var key = q.split('　')[0].trim();
    if (key.length < 3) return null;
    var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var node, hit = null;
    while ((node = w.nextNode())) {
      if (node.nodeValue.indexOf(key) < 0) continue;
      var el = node.parentElement;
      if (!el) continue;
      /* **画面に見えていない文字を拾わない。**
         この仕掛け自身が持っているデータ（JSON）に当たると、
         画面ぜんぶを囲んでしまう */
      if (/^(SCRIPT|STYLE|NOSCRIPT|TITLE)$/.test(el.tagName)) continue;
      if (layer.contains(el) || panel.contains(el) || bar.contains(el)) continue;
      while (el && el !== document.body && el.getBoundingClientRect().height < 8) {
        el = el.parentElement;
      }
      if (!el || el === document.body || el === document.documentElement) continue;
      var b = el.getBoundingClientRect();
      if (b.height > window.innerHeight * 1.2) continue;   // 大きすぎるものは的外れ
      hit = {x: b.left + window.scrollX, y: b.top + window.scrollY,
             w: b.width, h: b.height};
      break;
    }
    return hit;
  }

  function drawAll() {
    layer.querySelectorAll('.notemark').forEach(function (el) { el.remove(); });
    var s = docSize();
    layer.style.height = s.h + 'px';        // **面を画面の高さに合わせる**
    notes.forEach(function (n, i) {
      var box = findByQuote(n.quote), moved = false;
      if (!box) {
        if (!n.rect) return;
        var p = n.rect.split(',').map(Number);
        if (p.length !== 4 || p.some(isNaN)) return;
        box = {x: p[0] * s.w, y: p[1] * s.h, w: p[2] * s.w, h: p[3] * s.h};
        moved = true;                 // **元の場所が見つからない**
      }
      var el = document.createElement('button');
      el.type = 'button';
      el.className = 'notemark' + (moved ? ' moved' : '') +
                     (n.state === 'fixed' ? ' fixed' : '');
      el.style.left = box.x + 'px';
      el.style.top = box.y + 'px';
      el.style.width = box.w + 'px';
      el.style.height = box.h + 'px';
      el.innerHTML = '<span>' + (i + 1) + '</span><em>' +
        (n.state === 'fixed' ? '直しました　押して確認' : '送ったひとこと') + '</em>';
      el.title = n.body;
      n._box = box;
      n._moved = moved;
      el.addEventListener('click', function () { showRead(n, i + 1); });
      layer.appendChild(el);
    });
  }

  /* ---- 囲んだ中の文字を拾う。**あとで場所が動いても分かるように** ---- */
  function textIn(r) {
    var out = [], seen = {};
    var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    var node;
    while ((node = w.nextNode())) {
      var t = node.nodeValue.trim();
      if (!t || seen[t]) continue;
      var range = document.createRange();
      range.selectNodeContents(node);
      var b = range.getBoundingClientRect();
      if (!b.width && !b.height) continue;
      var x = b.left + window.scrollX, y = b.top + window.scrollY;
      if (x + b.width < r.x || x > r.x + r.w) continue;
      if (y + b.height < r.y || y > r.y + r.h) continue;
      seen[t] = 1;
      out.push(t);
      if (out.join(' ').length > 200) break;
    }
    return out.join('　').slice(0, 200);
  }

  /* ---- 囲む ---- */
  var start = null, ghost = null;

  function inApp(el) {
    return !panel.contains(el) && !bar.contains(el) && el !== openBtn &&
           !openBtn.contains(el) && !el.closest('.notemark') && !el.closest('.side');
  }

  document.addEventListener('mousedown', function (e) {
    if (!on() || e.button !== 0 || !inApp(e.target)) return;
    start = {x: e.pageX, y: e.pageY};
  });

  document.addEventListener('mousemove', function (e) {
    if (!start) return;
    var dx = Math.abs(e.pageX - start.x), dy = Math.abs(e.pageY - start.y);
    if (!ghost && dx < 8 && dy < 8) return;      // ただの押し込みは無視する
    if (!ghost) {
      ghost = document.createElement('div');
      ghost.className = 'noteghost';
      layer.appendChild(ghost);
      document.body.classList.add('nosel');
    }
    var r = norm(start, {x: e.pageX, y: e.pageY});
    ghost.style.left = r.x + 'px'; ghost.style.top = r.y + 'px';
    ghost.style.width = r.w + 'px'; ghost.style.height = r.h + 'px';
    e.preventDefault();
  });

  document.addEventListener('mouseup', function (e) {
    if (!start) return;
    var s0 = start; start = null;
    document.body.classList.remove('nosel');
    if (!ghost) return;                          // 動かしていなければ、ただのクリック
    var r = norm(s0, {x: e.pageX, y: e.pageY});
    ghost.remove(); ghost = null;
    if (r.w < 12 || r.h < 12) return;            // 小さすぎるものは事故
    askAbout(r);
  });

  function norm(a, b) {
    return {x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
            w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y)};
  }

  /* ---- 書いてもらう ---- */
  function askAbout(r) {
    var s = docSize();
    pending = {
      rect: r ? [r.x / s.w, r.y / s.h, r.w / s.w, r.h / s.h]
                 .map(function (v) { return v.toFixed(4); }).join(',') : '',
      quote: r ? textIn(r) : '',
      box: r,
    };
    title.textContent = r ? '囲んだところ' : 'この画面ぜんぶ';
    if (pending.quote) {
      quoteView.textContent = pending.quote;
      quoteView.hidden = false;
    } else { quoteView.hidden = true; }
    if (r) {
      var mark = document.createElement('div');
      mark.className = 'noteghost live';
      mark.style.left = r.x + 'px'; mark.style.top = r.y + 'px';
      mark.style.width = r.w + 'px'; mark.style.height = r.h + 'px';
      layer.appendChild(mark);
      pending.el = mark;
    }
    read.hidden = true; form.hidden = false;
    check.hidden = true;
    panel.hidden = false;
    place(r);
    body.value = '';
    body.focus();
  }

  function showRead(n, num) {
    title.textContent = num + '番目のひとこと';
    if (n.quote) { quoteView.textContent = n.quote; quoteView.hidden = false; }
    else { quoteView.hidden = true; }
    read.innerHTML = '';
    if (n._moved) {
      var warn = document.createElement('div');
      warn.className = 'movedhint';
      warn.textContent = 'この画面は書かれたあとに変わっています。囲みの位置はおおよそです。';
      read.appendChild(warn);
    }
    if (n.state === 'fixed') {
      var f = document.createElement('div');
      f.className = 'fixednote';
      f.textContent = '直しました' + (n.fixed_note ? '：' + n.fixed_note : '') +
                      '　直っているかご覧ください。';
      read.appendChild(f);
    }
    var p = document.createElement('p');
    p.textContent = n.body;
    var who = document.createElement('span');
    who.className = 'who';
    who.textContent = n.who;
    read.appendChild(p); read.appendChild(who);
    form.hidden = true; read.hidden = false;
    check.hidden = !(canCheck && n.state === 'fixed');
    ngbox.hidden = true;
    showing = n;
    panel.hidden = false;
    place(n._box || null);
  }

  function judge(url, payload) {
    fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json',
                'X-CSRFToken': csrf()},
      body: JSON.stringify(payload || {}),
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { say('うまくいきませんでした。'); return; }
      if (url.indexOf('/accept') > 0) {
        notes = notes.filter(function (x) { return x.id !== showing.id; });
        say('確認できたものとして閉じました。');
      } else {
        showing.state = 'open';
        say('差し戻しました。');
      }
      panel.hidden = true;
      drawAll();
    }).catch(function () { say('うまくいきませんでした。'); });
  }

  document.getElementById('noteok').addEventListener('click', function () {
    judge(endpoint + '/' + showing.id + '/accept');
  });
  document.getElementById('noteng').addEventListener('click', function () {
    ngbox.hidden = false;
    document.getElementById('notengnote').focus();
  });
  document.getElementById('notengsend').addEventListener('click', function () {
    judge(endpoint + '/' + showing.id + '/reopen',
          {reopen_note: document.getElementById('notengnote').value.trim()});
  });

  /* **囲んだ場所のとなりに出す。**画面の隅に出ると、どこの話か見失う */
  function place(r) {
    if (!r) {                                   // 画面ぜんぶへのひとことは隅でよい
      panel.classList.add('corner');
      panel.style.left = panel.style.top = '';
      return;
    }
    panel.classList.remove('corner');
    var pw = panel.offsetWidth || 340, ph = panel.offsetHeight || 260;
    var s = docSize();
    var x = r.x;
    var y = r.y + r.h + 10;
    if (y + ph > window.scrollY + window.innerHeight) {
      y = r.y - ph - 10;                        // 下に入らなければ上へ
    }
    if (y < window.scrollY + 8) y = window.scrollY + 8;
    if (x + pw > s.w - 12) x = s.w - pw - 12;
    if (x < 12) x = 12;
    panel.style.left = x + 'px';
    panel.style.top = y + 'px';
  }

  function closePanel() {
    panel.hidden = true;
    if (pending && pending.el) { pending.el.remove(); }
    pending = null;
  }
  document.getElementById('noteclose').addEventListener('click', closePanel);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closePanel();
  });

  openBtn.addEventListener('click', function () { askAbout(null); });

  document.getElementById('notesend').addEventListener('click', function () {
    var text = body.value.trim();
    if (!text) { body.focus(); return; }
    var btn = this;
    btn.disabled = true;
    fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json',
                'X-CSRFToken': csrf()},
      body: JSON.stringify({
        body: text, page: location.pathname, page_title: document.title,
        quote: pending ? pending.quote : '', rect: pending ? pending.rect : '',
      }),
    }).then(function (r) { return r.json(); }).then(function (d) {
      btn.disabled = false;
      if (!d.ok) { say(d.message || '送れませんでした。'); return; }
      notes.push(d.note);
      if (pending && pending.el) pending.el.remove();
      pending = null;
      panel.hidden = true;
      drawAll();
      bump();
      say('受け取りました。ありがとうございます。');
    }).catch(function () {
      btn.disabled = false;
      say('送れませんでした。もう一度お試しください。');
    });
  });

  /* 件数の表示を、送るたびに1つ増やす */
  function bump() {
    document.querySelectorAll('#notebar a, .modeswitch .n').forEach(function (el) {
      var m = el.textContent.match(/\d+/);
      if (m) el.textContent = el.textContent.replace(/\d+/, Number(m[0]) + 1);
    });
    var n = sw.querySelector('.n');
    if (!n) {
      n = document.createElement('span');
      n.className = 'n'; n.textContent = '1';
      sw.appendChild(n);
    }
  }

  window.addEventListener('resize', function () { if (on()) drawAll(); });
  paint();
})();
