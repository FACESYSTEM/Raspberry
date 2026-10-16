"""テーブル定義。**編集者を1人に決めること**（規約 §2）。

土台に必要なものだけを置いている。ドメインのモデルは `Partner` を見本として
1つだけ入れてあるので、**業務に合わせて作り直す**（消してよい）。

現実のデータには未採番・未紐付け・重複が**実在する**。
**一意制約を安易に付けない。NULL可を恐れない。**汚れているものは消さず、
「要確認」として一覧に出す（`Partner.issues`）。
"""
from datetime import date, timedelta
import secrets

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
    # **社外の方のとき、どの相手に属するか。**自社の人は空。
    # 役割が `outside` なのにここが空だと、1件も見せない（`scope_query`）。
    # **紐づけ忘れ＝全部見える、を作らない。**
    # 見本では取引先に繋いでいる。業務が決まったら、その実体に繋ぎ替える
    scope_partner_id = db.Column(db.Integer, db.ForeignKey("partners.id"),
                                 index=True)
    scope = db.relationship("Partner", foreign_keys=[scope_partner_id])
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

    @property
    def is_outside(self) -> bool:
        """顧問先ご本人か。**自社の人と混ぜない。**"""
        return self.role == "outside"

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
    同じ表に混ぜると、顧問先の対応履歴に開発の話が並ぶ。
    """
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(30), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)
    body = db.Column(db.Text, nullable=False)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=utcnow)

    author = db.relationship("User")


# 継続か過去か。**列で持たず、契約から導く**（`Client.phase`）
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
    ("partners",    "取引先",         True),     # 見本のドメイン
    ("org",         "利用者・組織",     True),
    ("choices",     "選択肢",         True),
    ("apikeys",     "外部連携の鍵",     False),
    ("credentials", "ログイン情報",     False),
    ("outside",     "社外の方の画面",    False),
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
    """選択肢のマスタ。**主担当・顧問種別のように、増えるが決まっているもの。**

    自由入力にすると表記がぶれる（実データでは同じ先が3通りに書かれていた）。
    かといってコードに埋めると、人が増えたときに開発を待つことになる。
    **管理画面から足せる形**にして、画面は選ぶだけにする。

    **消さずに `archived_at` で下げる。**過去のデータがその値を指しているため、
    消すと履歴が読めなくなる。
    """
    __tablename__ = "choices"

    id = db.Column(db.Integer, primary_key=True)
    group = db.Column(db.String(30), nullable=False, index=True)  # owner / service_kind
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
# 以下はひな形の見本。**作法を見せるためだけに残している**（消してよい）
# ---------------------------------------------------------------------------

# 選択肢の分類。**業務が決まったら、ここを業務の言葉で埋める。**
# 自由入力にすると表記が必ずぶれる（実案件では同じ相手が3通りに書かれていた）
CHOICE_GROUPS = {"owner": "主担当"}


class Partner(db.Model):
    """取引先。**一覧・詳細・インライン編集・要確認の作法を見せるための見本。**

    現実のデータに合わせて、**緩く持つ**。

      * `code` に一意制約を付けない（重複が実在することがある）
      * `code` は NULL可（未採番が実在する）
      * 消さずに `archived_at` で下げる（履歴と集計に要る）
    """
    __tablename__ = "partners"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), index=True)       # 未採番=NULL可・重複可
    name = db.Column(db.String(200), nullable=False)
    kind = db.Column(db.String(20))                   # 区分（見本）
    contact_name = db.Column(db.String(120))
    contact_email = db.Column(db.String(255))
    tel = db.Column(db.String(50))
    address = db.Column(db.String(300))
    note = db.Column(db.Text)
    archived_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow,
                           onupdate=utcnow)

    @property
    def issues(self) -> list:
        """要確認。**消さずに見せる。**正しさは制約ではなく検出で担保する。"""
        got = []
        if not self.code:
            got.append("未採番")
        if not self.contact_email:
            got.append("連絡先メールなし")
        return got
