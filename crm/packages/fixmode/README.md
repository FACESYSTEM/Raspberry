# 修正モード（fixmode）

**画面を見ている人が、その場で「ここを直して」と言えるようにする部品。**
言われた側が直し、**言った側が確認して閉じる**ところまでを持つ。

```
① 使い手が、直したい場所を四角く囲んでひとことを書く   → open   （オレンジの枠）
② 作り手が「直しました」と伝える（何を直したかも書ける） → fixed  （みどりの枠）
③ 使い手が枠を押して「直っています」で閉じる           → done   （枠が消える）
   直っていなければ「まだ直っていない」で ① に戻る
```

---

## なぜこの形か

**別紙にまとめて書いてもらう形は、うまくいかない。**
気づいた瞬間に書ける場所がなければ、次の画面へ進んだ時点で忘れる。
届いても「どの画面のどこの話か」が分からず、こちらが探すことになる。

だから、次の3つを守っている。

### 1　文字を選ばせず、四角で囲ませる

文字選択だと、**表の枠・空欄・余白など「文字が無いところ」を指せない。**
「ここに何か足りない」が一番言いたいことなのに、それが言えなくなる。
囲むだけなら、どこでも同じ操作で済む。

### 2　囲んだ中の文字も一緒に持つ

四角は「画面の幅・高さに対する割合」で持っている。
**画面の中身が変われば、この座標は合わなくなる。**
（実際、指摘を受けて1つ表を消しただけで、枠が別の場所を指した）

そこで、囲んだ中にあった文字も写して持つ。次に開くときは
**まずその文字を画面から探し、見つかればそこに枠を引き直す。**
見つからなければ元の座標に破線で描き、「位置はおおよそです」と断る。

### 3　直した人が閉じられない

`can_fix`（直す人）と `can_close`（閉じる人）を**別々に受け取る。**
同じ人が両方できると、**直したつもりが直っていない**まま通ってしまう。
差し戻しは何度でもでき、直した記録は消えない。

---

## 組み込む

### 1　置く

`packages/fixmode` を、そのままコピーする。読めるように通しておく。

```python
# config.py など
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages"))
```

### 2　繋ぐ

```python
from flask_login import current_user, login_required
from fixmode import FixMode

FixMode(db).init_app(
    app,
    can_comment=lambda: current_user.is_authenticated,   # 書ける人
    can_fix=is_developer,                                # 直す人（作り手）
    can_close=is_admin,                                  # 閉じる人（使い手）
    who=lambda: (current_user.id, current_user.name),
    login_required=login_required,      # 無ければ省略できる
    url_prefix="/notes")                # 既定は /notes
```

`can_fix` と `can_close` は**必ず別のものにする。**
同じにすると、この部品を入れる意味が半分なくなる。
同じものを渡すと**起動時に警告を出す**（止めはしない。小さな組では
兼ねることもあるため）。ただし見分けられるのは「同じものを渡した」ときだけで、
中身が同じ別の関数までは分からない。

### 3　画面に差し込む（`base.html` の3か所）

```jinja
<head>
  <meta name="csrf-token" content="{{ csrf_token() }}">   {# CSRFを使う場合 #}
</head>
<body>
  {% include "fixmode/widget.html" %}      {# 本文のいちばん先 #}

  <nav> … {% include "fixmode/switch.html" %} </nav>   {# 入／切の切り替え #}

  {% include "fixmode/script.html" %}      {# </body> の直前 #}
</body>
```

一覧への導線も、どこかに置く。

```jinja
<a href="{{ url_for('fixmode.index') }}">届いたひとこと
  {% if FIXMODE_OPEN %}<span class="badge">{{ FIXMODE_OPEN }}</span>{% endif %}</a>
```

### 4　表を作る

`fixmode_notes` が1つ増えるだけ。既にある表には触らない。

```python
with app.app_context():
    db.create_all()
```

`db.create_all()` を使わない場合は、これを流す（SQLite）。

```sql
CREATE TABLE fixmode_notes (
  id            INTEGER PRIMARY KEY,
  page          VARCHAR(200) NOT NULL,
  page_title    VARCHAR(120),
  rect          VARCHAR(60),
  quote         TEXT,
  body          TEXT NOT NULL,
  state         VARCHAR(10) NOT NULL DEFAULT 'open',
  author_id     INTEGER,
  author_name   VARCHAR(120),
  created_at    DATETIME,
  fixed_at      DATETIME,
  fixed_by_name VARCHAR(120),
  fixed_note    TEXT,
  reopen_note   TEXT,
  done_at       DATETIME,
  done_by_name  VARCHAR(120)
);
CREATE INDEX ix_fixmode_notes_page       ON fixmode_notes (page);
CREATE INDEX ix_fixmode_notes_state      ON fixmode_notes (state);
CREATE INDEX ix_fixmode_notes_created_at ON fixmode_notes (created_at);
```

**利用者の表への外部キーは張っていない。**名前は書いた時点で写して持つ。

### 5　動くか確かめる

```bash
python -m pytest packages/fixmode/tests -q      # 13本
```

**この部品だけで動くことを確かめる試験。**
Flask-Login も Flask-WTF も使わない最小の土台に載せて試している。
ここが通らなければ、繋ぎ方（`can_fix` と `can_close`）を見直す。

そのあと、実際の画面で次の順に触る。

1. 切り替えを押して、上に黒い帯が出るか
2. ドラッグで四角が描けるか（**8ピクセル以上動かす**。ただの押し込みは無視する）
3. 送ると、画面が切り替わらずに枠が残るか
4. `can_fix` の人で「直しました」、`can_close` の人で「直っています」まで通るか

---

## 前提と、無くても動くもの

| | |
|---|---|
| 要る | Flask、Flask-SQLAlchemy |
| 無くても動く | Flask-Login（`login_required` を渡さなければ素通し） |
| 無くても動く | Flask-WTF（`meta[name=csrf-token]` が無ければ送らない） |
| 無くても動く | 土台の `toast()`（無ければ自前で一言を出す） |
| 無くても動く | 土台のボタンの見た目（最低限の見た目を自分で持つ） |

**利用者の表とは繋いでいない。** 名前は書いた時点で写して持つ。
持っていく先で利用者の作りが違っても、そのまま動かすため。

---

## 色を合わせる

`fixmode.css` の先頭にある変数を、土台の色で上書きする。

```css
:root{
  --fix-accent:#2f6f4f;   /* 主な色 */
  --fix-good:#2f7d4f;     /* 直した（みどりの枠） */
  --fix-warn:#a8790f;     /* まだ直していない（オレンジの枠） */
  --fix-serious:#b3402c;  /* 差し戻し */
  --fix-line:#e5e3df;
  --fix-soft:#faf9f7;
  --fix-muted:#9a968e;
  --fix-ink2:#6b6862;
}
```

---

## 中身

```
fixmode/
  __init__.py                    FixMode を出すだけ
  core.py                        表・経路・画面への差し込み
  templates/fixmode/
    widget.html                  帯・囲みの面・書く欄・丸いボタン
    switch.html                  入／切の切り替え
    script.html                  script タグ（受け渡しの値つき）
    list.html                    届いたものの一覧
  static/
    fixmode.js                   囲む・探し直す・送る・確認する
    fixmode.css                  見た目（変数で上書きできる）
```

## 経路

| | |
|---|---|
| `POST /notes` | 書く（JSON／フォームのどちらでも） |
| `GET  /notes` | 一覧 |
| `POST /notes/<id>/fixed` | 直した（`can_fix` の人だけ） |
| `POST /notes/<id>/accept` | 確認して閉じる（`can_close` の人だけ） |
| `POST /notes/<id>/reopen` | 差し戻す（`can_close` の人だけ） |

`accept` と `reopen` は、JSONで呼べば画面を飛ばさずに返す。
枠を押してその場で終われるようにするため。

## 覚えていること

入／切は端末ごとに `localStorage['fix_mode']` で覚える。
**普段の画面には何も出さない。**見た目が変わると、それ自体が指摘の的になる。

## 気をつけたところ

* 画面に見えていない文字は探さない。
  **この部品自身が持っているデータ**（画面下のJSON）を拾うと、
  親をたどって画面ぜんぶを囲んでしまう。
* 大きすぎる要素は的外れとみなす（画面の高さの1.2倍を超えるもの）。
* `hidden` は `display` を持つ要素に負ける。`[hidden]{display:none}` を明示している。
* 表の定義は1度だけ。アプリを作り直すたびに定義すると二重登録で落ちる。
  取っておく鍵は `db` そのもの（`id()` は片づけられたあと使い回される）。
* **開けない人にリンクを出さない。**一覧は `can_fix` か `can_close` の人だけが
  開けるので、リンクと件数もその条件で出す。
  書くだけの人に「届いているもの 3件」と見せて、押すと403は最悪。
* 件数は**全画面ぶんの合計**。一覧を開ける人にしか出さないのは、そのため。
