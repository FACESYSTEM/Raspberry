"""テーブル定義。**編集者を1人に決めること**（規約 §2）。

前半は土台（利用者・履歴・自社の情報など）、後半が業務（ゲスト・キャスト・
ボトル・来店）。言葉は規約 §7 の辞書に合わせる。

現実のデータには未採番・未紐付け・重複が**実在する**（Notion の書き出しで
ゲスト番号の空2件・重複1組を確認した）。**一意制約を安易に付けない。NULL可を
恐れない。**汚れているものは消さず、「要確認」として一覧に出す（`Guest.issues`）。
"""
from datetime import date, timedelta
import secrets

from flask import g, has_request_context

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager
from .timeutil import utcnow


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # **パスワードはハッシュのみ。**平文を持たない・ログに出さない
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), nullable=False, default="staff")
    is_active_user = db.Column(db.Boolean, nullable=False, default=True)

    # 招待・再設定。**使い捨て**にして、使ったら消す
    invite_token = db.Column(db.String(64), index=True)
    invite_expires_at = db.Column(db.DateTime)

    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow,
                           onupdate=utcnow)

    # ---- パスワード ----
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)
        self.invite_token = None            # 使ったら消す
        self.invite_expires_at = None

    def check_password(self, raw: str) -> bool:
        return bool(self.password_hash) and \
            check_password_hash(self.password_hash, raw or "")

    # ---- 招待 ----
    def issue_invite(self, hours: int = 72) -> str:
        """招待の合言葉を作る。**戻り値は1度しか手に入らない扱いにする。**"""
        token = secrets.token_urlsafe(32)
        self.invite_token = token
        self.invite_expires_at = utcnow() + timedelta(hours=hours)
        return token

    @property
    def invite_alive(self) -> bool:
        return bool(self.invite_token and self.invite_expires_at
                    and self.invite_expires_at > utcnow())

    # Flask-Login。**無効にした利用者は入れない**
    @property
    def is_active(self) -> bool:
        return bool(self.is_active_user)


class ChangeHistory(db.Model):
    """誰が・いつ・どの項目を・何から何へ。

    **これを伴わない更新を書かない。**数字が食い違ったとき、履歴が無いと
    原因に辿り着けない。

    **秘密の値は入れない。**履歴から漏れては意味がない（「変更しました」とだけ書く）。
    """
    __tablename__ = "change_history"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(30), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    field = db.Column(db.String(60), nullable=False)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow, index=True)

    user = db.relationship("User")

    @staticmethod
    def record(entity_type: str, entity_id: int, field: str,
               old, new, user_id=None) -> "ChangeHistory":
        h = ChangeHistory(entity_type=entity_type, entity_id=entity_id,
                          field=field,
                          old_value="" if old is None else str(old),
                          new_value="" if new is None else str(new),
                          user_id=user_id)
        db.session.add(h)
        return h


class Comment(db.Model):
    """対応履歴。どの実体にも付けられるようにしておく。

    **画面への指摘は別のもの**（`packages/fixmode`）。
    同じ表に混ぜると、ゲストの対応履歴に開発の話が並ぶ。
    """
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(30), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    author = db.relationship("User")


# 自社の法人。**1つから始める。**
# 請求の計上先が分かれている会社では、ここに足して使い分ける
# （実案件では、請求書の保管フォルダが2法人に分かれていた）。
# **コードに社名を埋めない。**画面から直せるようにしておく
COMPANIES = ("main", "sub")
COMPANY_LABEL = {"main": "自社", "sub": "別法人"}


class OrgProfile(db.Model):
    """自社の情報。**法人ごとに1行。**

    請求書の保管フォルダが「株式会社計上」「合同会社計上」に分かれており、
    **法人が2つある。**1行しか持てないと、請求書の発行元を出し分けられない。

    会社名・住所・ロゴのように**コードに埋めたくなるもの**をここに置く
    （設計指示書 §10.2）。埋めると別の組織へ持っていくときに書き換えて回ることになる。

    **画面に出す名前もここから取る。**`.env` と二重に持つと、
    管理画面で変えても画面が変わらず、設定したつもりで効かない状態になる。
    """
    __tablename__ = "org_profile"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), index=True)       # kk / gk（規約 §7.3）
    name = db.Column(db.String(200))
    # **画面の見出しに出す法人。**1つだけ true にする
    is_display = db.Column(db.Boolean, nullable=False, default=False)
    postal_code = db.Column(db.String(10))
    address = db.Column(db.String(300))
    tel = db.Column(db.String(50))
    email = db.Column(db.String(255))
    # 請求書に要るもの。**請求を作る前に埋まっていないと出せない**
    invoice_reg_no = db.Column(db.String(40))         # 適格請求書発行事業者の登録番号
    bank = db.Column(db.String(300))                  # 振込先
    seal_url = db.Column(db.String(500))              # 社印・ロゴ
    # 連絡が途切れたと見なす日数の組織既定。**画面から変えられるようにする**
    stale_days = db.Column(db.Integer)
    # 契約更新の何日前から知らせるか。**過ぎてから気づくのでは遅い**（要望 §2-1）
    renew_notice_days = db.Column(db.Integer)
    # いま使っている法人か。**使っていない法人の要確認を出し続けない。**
    # 消えない警告は読まれなくなり、本当に見てほしいものまで埋もれる（土台 §3.4）。
    # ただし**行そのものは消さない。**過去の請求書の発行元を表示するのに要る
    in_use = db.Column(db.Boolean, nullable=False, default=True)
    note = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    @staticmethod
    def renew_notice() -> int:
        """契約更新の何日前から知らせるか。**表が無くても落とさない。**"""
        try:
            got = OrgProfile.display()
            if got and got.renew_notice_days:
                return got.renew_notice_days
        except Exception:                       # noqa: BLE001
            pass
        return RENEW_NOTICE_DAYS

    @staticmethod
    def all_companies() -> list:
        """2法人ぶん。**無ければ作る**（画面から編集できるように）。"""
        for code, label in COMPANY_LABEL.items():
            if not OrgProfile.query.filter_by(code=code).first():
                db.session.add(OrgProfile(
                    code=code, name=label,
                    is_display=(code == COMPANIES[0])))
        db.session.commit()
        return (OrgProfile.query.order_by(OrgProfile.code.desc()).all())

    @staticmethod
    def display() -> "OrgProfile":
        """画面の見出しに出す法人。**印が無ければ最初の1つ。**"""
        rows = OrgProfile.all_companies()
        return next((r for r in rows if r.is_display), rows[0])

    @staticmethod
    def get() -> "OrgProfile":
        """後方互換。`display()` と同じ。"""
        return OrgProfile.display()

    @property
    def label(self) -> str:
        return COMPANY_LABEL.get(self.code, self.name or "")

    @property
    def issues(self) -> list:
        """請求書を出す前に埋まっている必要があるもの。**消さずに見せる。**

        使っていない法人には出さない。**直しようのない警告は、
        出し続けるほど全体が読まれなくなる。**
        """
        if not self.in_use:
            return []
        got = []
        if not self.name:
            got.append("名称が未記入")
        if not self.address:
            got.append("住所が未記入")
        if not self.invoice_reg_no:
            got.append("適格請求書発行事業者の登録番号が未記入")
        if not self.bank:
            got.append("振込先が未記入")
        return got


class ApiKey(db.Model):
    """外部に渡す鍵。**平文は保存しない。**

    渡す相手に本番の `.env` を見せるわけにいかないので、画面から発行して
    **1度だけ表示する**。照合はハッシュで行う。
    用途ごとに分けるのは、片方を止めたいときにもう片方を巻き込まないため。
    """
    __tablename__ = "api_keys"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)     # 渡す先が分かる名前
    scope = db.Column(db.String(40), nullable=False, default="default")
    prefix = db.Column(db.String(16), nullable=False, index=True)
    key_hash = db.Column(db.String(255), nullable=False)
    note = db.Column(db.String(200))
    last_used_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def alive(self) -> bool:
        return self.revoked_at is None


class Credential(db.Model):
    """外部サービスのログイン情報。**パスワードは暗号化して持つ。**

    照合するだけの `ApiKey` と違い、あとで人が読む必要があるのでハッシュに
    できない。暗号化は `secretbox`（鍵は `.env`）。

    **一覧に平文を出さない。**押した1件だけ返し、見たことを履歴に残す。
    """
    __tablename__ = "credentials"

    id = db.Column(db.Integer, primary_key=True)
    group = db.Column(db.String(40), index=True)      # 製品・システムの名前
    name = db.Column(db.String(120), nullable=False)  # 本番 / 開発 など
    url = db.Column(db.String(300))
    login_id = db.Column(db.String(200))
    password_enc = db.Column(db.Text)                 # 暗号化済み
    note = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow,
                           onupdate=utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))


# **公開／非公開は最初から持つ。**作りかけを先方に見せない。
# 足すときは、ここに1行足して画面から切り替える
FEATURES = [
    ("guests",      "ゲスト",         True),
    ("casts",       "キャスト",        True),
    ("org",         "利用者・組織",     True),
    ("choices",     "選択肢",         True),
    ("apikeys",     "外部連携の鍵",     False),
    ("credentials", "ログイン情報",     False),
]
FEATURE_LABEL = {k: label for k, label, _ in FEATURES}


class Feature(db.Model):
    """機能の公開状態。**マスターだけが切り替えられる。**

    先方には**公開した機能だけ**を見せる。順に見てもらうため、
    こちらの都合で出し入れできる形にしておく。

    **画面から隠すだけでは足りない。**URLを直に叩けば入れてしまうので、
    経路そのものを止める（`feature_required`）。
    """
    __tablename__ = "features"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(40), nullable=False, unique=True, index=True)
    released = db.Column(db.Boolean, nullable=False, default=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    @staticmethod
    def all_rows() -> list:
        """定義されている機能ぶん。**無ければ既定値で作る。**"""
        have = {f.key: f for f in Feature.query.all()}
        add = [Feature(key=k, released=d) for k, _, d in FEATURES if k not in have]
        if add:
            db.session.add_all(add)
            db.session.commit()
            have.update({f.key: f for f in add})
        return [have[k] for k, _, _ in FEATURES]

    @staticmethod
    def is_open(key: str) -> bool:
        """公開されているか。**表が無くても落とさない**（初期化前でも画面を出す）。"""
        try:
            row = Feature.query.filter_by(key=key).first()
            if row is not None:
                return bool(row.released)
        except Exception:                       # noqa: BLE001
            pass
        return dict((k, d) for k, _, d in FEATURES).get(key, False)


class Choice(db.Model):
    """選択肢のマスタ。**担当レベルのように、増えるが決まっているもの。**

    自由入力にすると表記がぶれる（実データでは同じ先が3通りに書かれていた）。
    かといってコードに埋めると、人が増えたときに開発を待つことになる。
    **管理画面から足せる形**にして、画面は選ぶだけにする。

    **消さずに `archived_at` で下げる。**過去のデータがその値を指しているため、
    消すと履歴が読めなくなる。
    """
    __tablename__ = "choices"

    id = db.Column(db.Integer, primary_key=True)
    group = db.Column(db.String(30), nullable=False, index=True)  # level
    label = db.Column(db.String(80), nullable=False)
    sort_order = db.Column(db.Integer, default=100)
    archived_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    @staticmethod
    def options(group: str, current=None) -> list:
        """選べる値。**いま入っている値が一覧に無くても落とさない。**

        移行で入った値がマスタに無いことは実在する。黙って消えると、
        保存した瞬間に値が失われる（土台 §4.4「人が入れた値を上書きしない」）。
        """
        rows = (Choice.query.filter_by(group=group)
                .filter(Choice.archived_at.is_(None))
                .order_by(Choice.sort_order, Choice.id).all())
        got = [r.label for r in rows]
        if current and current not in got:
            got.append(current)          # **現在の値を必ず残す**
        return got


# ---------------------------------------------------------------------------
# 業務。言葉は規約 §7 の辞書どおり
# ---------------------------------------------------------------------------

# 選択肢の分類。**自由入力にすると表記がぶれるものだけ**をここで持つ
CHOICE_GROUPS = {"level": "担当レベル"}


def guest_label(no, name) -> str:
    """画面に出すゲストの名前。**番号と名前から毎回作る**（規約 §7.6）。

    Notion ではタイトル列に `【Gu_1592】〇〇` と書いて持っていたが、
    番号や名前を直してもタイトルだけ古いまま残っていた。持たずに導く。
    """
    head = f"Gu_{no}" if no is not None else "Gu_（番号なし）"
    return f"{head} {name}" if name else head


class Corp(db.Model):
    """法人・団体。ゲストの勤め先・所属。

    **名前に一意制約を付けない。**`㈱〇〇` と `株式会社〇〇` のような
    表記ゆれが実在し、同じ会社かどうかは人が見て決めることだから。
    """
    __tablename__ = "corps"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    kana = db.Column(db.String(200))
    archived_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)


class Cast(db.Model):
    """キャスト。

    `cast_no` は Notion の `CA_25_〇〇` の 25。**番号の無いキャストが実在する**
    （`CA_Noなし_〇〇`）ので NULL可・一意にしない。
    辞めたキャストも**消さずに下げる**（`archived_at`）。担当の履歴が読めなくなるため。
    """
    __tablename__ = "casts"

    id = db.Column(db.Integer, primary_key=True)
    cast_no = db.Column(db.Integer, index=True)
    name = db.Column(db.String(80), nullable=False)
    note = db.Column(db.Text)
    archived_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def label(self) -> str:
        """`CA_25_〇〇`。番号が無ければ `CA_Noなし_〇〇`（Notion と同じ書き方）。"""
        no = self.cast_no if self.cast_no is not None else "Noなし"
        return f"CA_{no}_{self.name}"

    @property
    def active_assignments(self) -> list:
        """いま担当しているもの。**数は持たずにここから導く**（規約 §7.6）。"""
        return [a for a in self.assignments
                if a.ended_at is None and a.guest and not a.guest.archived_at]

    @property
    def issues(self) -> list:
        return [] if self.cast_no is not None else ["キャスト番号なし"]


class Guest(db.Model):
    """ゲスト。**Notion の1行がこれ1件。**

    Notion では1行にボトル・担当・来店まで詰めていたが、ここでは分ける
    （`Bottle`・`Assignment`・`Visit`）。最終来店日・来店回数・いまの担当は
    **列で持たず、そこから導く。**両方持つと片方だけ古くなる。
    """
    __tablename__ = "guests"

    id = db.Column(db.Integer, primary_key=True)
    # **未採番=NULL可・重複可。**実データにどちらもある（規約 §7.1）
    guest_no = db.Column(db.Integer, index=True)
    # 名前が分からないゲストも実在する（Notion で18件）。**空でも登録できる**
    name = db.Column(db.String(120))
    kana = db.Column(db.String(120))
    corp_id = db.Column(db.Integer, db.ForeignKey("corps.id"), index=True)
    title = db.Column(db.String(120))
    neck_name = db.Column(db.String(120))
    # **年は分からないことが多い**（規約 §7.2）。分からなければ空のまま
    birth_year = db.Column(db.Integer)
    birth_month = db.Column(db.Integer)
    birth_day = db.Column(db.Integer)
    tel = db.Column(db.String(50))
    email = db.Column(db.String(255))
    address = db.Column(db.String(300))
    note = db.Column(db.Text)
    registered_on = db.Column(db.Date)
    # **担当なしを確かめたか。**空の担当を「担当なし」と読まないため（規約 §7.3）
    no_cast = db.Column(db.Boolean, nullable=False, default=False)
    archived_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    corp = db.relationship("Corp", backref="guests")

    @property
    def label(self) -> str:
        return guest_label(self.guest_no, self.name)

    # ---- 導くもの（規約 §7.6）----
    @property
    def live_visits(self) -> list:
        return sorted((v for v in self.visits if v.archived_at is None),
                      key=lambda v: v.visited_on, reverse=True)

    @property
    def last_visit_on(self):
        """最終来店日。**記録が無ければ None**（来ていない、ではない）。"""
        got = self.live_visits
        return got[0].visited_on if got else None

    @property
    def visit_count(self) -> int:
        return len(self.live_visits)

    @property
    def active_assignments(self) -> list:
        return [a for a in self.assignments if a.ended_at is None]

    @property
    def cast_state(self) -> str:
        """担当の状態。`has`（いる）・`none`（いないと確かめた）・`unknown`。"""
        if self.active_assignments:
            return "has"
        return "none" if self.no_cast else "unknown"

    @property
    def keeping_bottles(self) -> list:
        return [b for b in self.bottles if b.finished_at is None]

    @property
    def birthday_text(self) -> str:
        """誕生日の表示。**分からない部分は書かない**（年を今年で埋めない）。"""
        if not self.birth_month:
            return ""
        md = f"{self.birth_month}月{self.birth_day}日" if self.birth_day \
            else f"{self.birth_month}月（日は分からない）"
        return f"{self.birth_year}年{md}" if self.birth_year else md

    @property
    def age(self):
        """年齢。**年が分からなければ出さない。**"""
        if not (self.birth_year and self.birth_month and self.birth_day):
            return None
        t = date.today()
        return t.year - self.birth_year - (
            (t.month, t.day) < (self.birth_month, self.birth_day))

    @property
    def issues(self) -> list:
        """要確認。**消さずに見せる。**正しさは制約ではなく検出で担保する。"""
        got = []
        if self.guest_no is None:
            got.append("番号なし")
        elif self.guest_no in duplicate_guest_nos():
            got.append("番号が重複")
        if not self.name and not self.kana:
            got.append("名前なし")
        if self.cast_state == "unknown":
            got.append("担当が分からない")
        if self.birth_month and not self.birth_day:
            got.append("誕生日の日が分からない")
        return got


def duplicate_guest_nos() -> set:
    """重複しているゲスト番号。**1回の表示につき1回だけ数える。**

    ゲストごとに数えると、2,000件の一覧で2,000回問い合わせることになる。
    """
    if has_request_context() and "_dup_guest_nos" in g:
        return g._dup_guest_nos
    rows = (db.session.query(Guest.guest_no)
            .filter(Guest.guest_no.isnot(None), Guest.archived_at.is_(None))
            .group_by(Guest.guest_no)
            .having(db.func.count(Guest.id) > 1).all())
    got = {r[0] for r in rows}
    if has_request_context():
        g._dup_guest_nos = got
    return got


class Assignment(db.Model):
    """担当。ゲストとキャストの結び付き。**1人のゲストに複数ありうる。**

    外れたら消さずに `ended_at` を入れる。誰がいつまで担当していたかが
    読めなくなると、引き継ぎで困る。
    """
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    guest_id = db.Column(db.Integer, db.ForeignKey("guests.id"),
                         nullable=False, index=True)
    cast_id = db.Column(db.Integer, db.ForeignKey("casts.id"),
                        nullable=False, index=True)
    # 選択肢（`level`）。**既定値を置かない**（未選択は未選択として出す）
    level = db.Column(db.String(30))
    # `（A）`・`（C）` など。意味が決まるまで書かれたまま持つ（規約 §7.3）
    mark = db.Column(db.String(30))
    ended_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    guest = db.relationship("Guest", backref="assignments")
    cast = db.relationship("Cast", backref="assignments")


class Bottle(db.Model):
    """キープボトル。**1本＝1行。**

    Notion では `知多⑯P マッカラン⑩P` のように1つの欄に何本も書いていた。
    `label` には**書かれたとおり**を残し、銘柄・番号・置き場所は分かる分だけ持つ。
    読み違いがあっても元に戻れるようにするため。
    """
    __tablename__ = "bottles"

    id = db.Column(db.Integer, primary_key=True)
    guest_id = db.Column(db.Integer, db.ForeignKey("guests.id"),
                         nullable=False, index=True)
    label = db.Column(db.String(200), nullable=False)
    brand = db.Column(db.String(80))
    keep_no = db.Column(db.Integer)
    place = db.Column(db.String(120))
    # 飲み切り。**消さずに下げる**
    finished_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)

    guest = db.relationship("Guest", backref="bottles")


class Visit(db.Model):
    """来店。**1回＝1行。**最終来店日・来店回数はここから導く。

    打ち間違いは消さずに `archived_at` で取り消す。
    """
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    guest_id = db.Column(db.Integer, db.ForeignKey("guests.id"),
                         nullable=False, index=True)
    visited_on = db.Column(db.Date, nullable=False, index=True)
    note = db.Column(db.String(300))
    archived_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    guest = db.relationship("Guest", backref="visits")
