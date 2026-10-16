"""権限・スコープ・秘密の比較。**ここを通さない経路を作らない。**"""
from functools import wraps

from flask import abort, session
from flask_login import current_user

# **どの役割の見え方で確認中か**を入れておく場所。
# 別のアカウントを作らずに、ログインし直さずに確認できるようにする。
# 見え方を確かめずに公開すると、隠したつもりのものが見えている事故が起きる。
#
# **役割ごとに用意する。**「管理者だけ確認できる」だと、担当者・閲覧のみ・
# 顧問先の見え方は誰も見ないまま公開されることになる。
PREVIEW_KEY = "preview_as"
PREVIEW_SCOPE_KEY = "preview_scope_id"

# 上ほど強い。**役割は最初から複数用意する。**
# 全員が管理者から始めると、あとで割ることになり必ず漏れる。
#
# `master` は**開発側**（当社）のための役割。作りかけの機能も見える。
# 先方の管理者（`owner`）には、**公開した機能だけ**を見せる。
# `outside` は**社外の方**。自分に紐づくものだけが見える（`scope_query`）。
#
# **実在しない役割を置かない。**
# 使われない役割は、選ぶときに迷わせるだけ。必要になったらここに足す
ROLES = ("master", "owner", "staff", "outside")
ROLE_LABEL = {"master": "マスター", "owner": "管理者", "staff": "担当者",
              "outside": "社外"}

# 無くした役割の行き先。**古いデータを弾かない**（土台 §4.1）
RETIRED_ROLES = {"manager": "staff", "guest": "staff"}

# 自社の人の役割。**社外の方はここに入らない**
STAFF_ROLES = ("master", "owner", "staff")

# 社外の方の役割。**何を見せるかが決まるまで、何も見せない。**
# 決まったら、機能「社外の方の画面」を公開する
OUTSIDE_ROLES = ("outside",)

# 確認モードで名乗れる役割。**マスターは入れない。**
# マスターの見え方を確認する必要はないし、抜けられなくなる
PREVIEWABLE = ("owner", "staff", "outside")


def role_required(*roles):
    """指定した役割のみ通す。**足りなければ403。**

    `master` はすべて通す。個々の指定に `master` を書き足して回ると、
    **書き忘れた画面だけ入れなくなる**ので、ここで一括して扱う。
    """
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            # **確認モード中は、名乗っている役割で判定する。**
            # ここを甘くすると「自分だけ見えていた」に気づけない
            role = effective_role()
            if role != "master" and role not in roles:
                abort(403)
            return fn(*a, **kw)
        return inner
    return deco


def real_is_master() -> bool:
    """本当にマスターか。**確認モード中でも変わらない。**

    確認モードを抜けるための経路だけは、これで判定する。
    抜けられなくなると、ログインし直すしかなくなる。
    """
    return getattr(current_user, "role", None) == "master"


def previewing() -> bool:
    """いま別の役割の見え方で確認中か。"""
    return bool(preview_role())


def preview_role():
    """確認中の役割。確認していなければ `None`。

    **マスター以外が触っても効かない。**セッションに値が残っていても、
    本人がマスターでなければ無視する。
    """
    if not real_is_master():
        return None
    role = session.get(PREVIEW_KEY)
    return role if role in PREVIEWABLE else None


def preview_scope_id():
    """社外の方として確認するとき、どの相手として見るか。"""
    if preview_role() != "outside":
        return None
    return session.get(PREVIEW_SCOPE_KEY)


def effective_role():
    """**いま何者として扱うか。**判定はすべてここを見る。

    `current_user.role` を直に見る場所を残すと、そこだけ確認モードが
    効かず、「確認したはずなのに見えていた」が起きる。
    """
    role = preview_role() or getattr(current_user, "role", None)
    return RETIRED_ROLES.get(role, role)


def is_master() -> bool:
    """開発側として扱うか。**確認モード中は「違う」と答える。**

    ここが本物と同じ答えを返すと、確認にならない。
    """
    return real_is_master() and not previewing()


def is_owner() -> bool:
    """管理者以上か。**マスターは管理者を兼ねる。**"""
    return effective_role() in ("master", "owner")


def can_edit() -> bool:
    """社外の方には書かせない。**OEM・顧問先は見るだけ。**"""
    return effective_role() in STAFF_ROLES


def scope_query(query, model, user=None):
    """見てよい範囲に絞る。**画面に出すクエリは必ずここを通す。**

    **顧問先ご本人には、自分の会社のことしか見せない。**
    ここを通していない画面が1つでもあると、そこから他社の情報が漏れる。
    だから土台の時点で全画面の呼び出し口を通してある。

    自社の人（`STAFF_ROLES`）は全件。将来、担当分だけに絞るときも、
    **ここに足すだけで全画面に効く。**
    """
    u = user or current_user
    if not getattr(u, "is_authenticated", False):
        # ログインしていない相手には1件も返さない
        return query.filter(False)

    # **確認モードは、いまログインしている本人にだけ効かせる。**
    # 別の人を指定して呼ばれたときまで効かせると、答えが入れ替わる
    mine = user is None or user is current_user
    role = effective_role() if mine else getattr(u, "role", None)

    if role in OUTSIDE_ROLES:
        if not feature_open("outside"):
            # **まだ社外には出さない。**器だけ用意してある状態
            return query.filter(False)
        sid = (preview_scope_id() if mine else None) or \
            getattr(u, "scope_partner_id", None)
        if not sid:
            # **紐づけ忘れ＝全部見える、を作らない**
            return query.filter(False)
        from .models import Partner                           # noqa: PLC0415
        if model is Partner:
            return query.filter(model.id == sid)
        if hasattr(model, "partner_id"):
            return query.filter(model.partner_id == sid)
        return query.filter(False)

    return query


def feature_open(key: str) -> bool:
    """その機能を、いまの人に見せてよいか。

    **マスターには作りかけも見せる。**それ以外には公開したものだけ。
    """
    if is_master():
        return True
    from .models import Feature                              # noqa: PLC0415
    return Feature.is_open(key)


def feature_required(key: str):
    """公開していない機能への経路を止める。

    **画面から隠すだけでは足りない。**URLを直に叩けば入れてしまう。
    ここで止めることで、隠し忘れが起きない（土台 §3.1 と同じ考え方）。
    """
    def deco(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            if not feature_open(key):
                abort(404)          # **403にしない。**あることを知らせない
            return fn(*a, **kw)
        return inner
    return deco


def same_secret(a: str, b: str) -> bool:
    """秘密の文字列が一致するか。**時間差で当てられないように比べる。**

    `hmac.compare_digest` は**文字列だと非ASCIIで例外を投げる**。
    バイト列にしてから比べれば中身を問わない。
    """
    import hmac
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------- 一覧表
# **判定そのものから作る。**表を手で書くと、コードを直したときに
# 表だけ古くなり、「表ではできないはずが、実際はできる」が起きる。
# **判定そのものから作る。**表を手で書くと、コードを直したときに
# 表だけ古くなり、「表ではできないはずが、実際はできる」が起きる
CAPABILITY_ROWS = (
    ("取引先の閲覧", "", False,
     lambda r: "自分の分だけ" if r in OUTSIDE_ROLES else "全件"),
    ("取引先の追加・編集", "", False,
     lambda r: "○" if r in STAFF_ROLES else "×"),
    ("利用者の招待・役割の変更", "", True,
     lambda r: "○" if r in ("master", "owner") else "×"),
    ("自社の情報・選択肢の管理", "", True,
     lambda r: "○" if r in ("master", "owner") else "×"),
    ("機能の公開／非公開の切り替え", "", True,
     lambda r: "○" if r == "master" else "×"),
    ("外部連携の鍵・ログイン情報", "非公開", True,
     lambda r: "○" if r == "master" else "×"),
    ("自分のパスワード変更", "", False, lambda r: "○"),
)


# 強い順。表の列の並びでもある
ROLE_ORDER = ("master",) + PREVIEWABLE


def visible_roles(role=None):
    """その人に見せてよい役割の範囲。**自分より上は出さない。**

    自分より強い役割の一覧が見えると、「そこには何があるのか」を
    確かめたくなる。**あることを知らせない**（`feature_required` と同じ考え方）。
    """
    role = role or effective_role()
    if role not in ROLE_ORDER:
        return ()
    return ROLE_ORDER[ROLE_ORDER.index(role):]


def capability_matrix(roles=None):
    """役割ごとにできることの一覧。画面に出すためのもの。

    **どの列にも○が付かない行は出さない。**自分より上だけができることを
    「全部×」の行として並べると、結局そこに何かあると分かってしまう。
    """
    roles = tuple(roles) if roles else ROLE_ORDER
    rows = []
    for what, note, strong, fn in CAPABILITY_ROWS:
        by = {r: fn(r) for r in roles}
        if all(v == "×" for v in by.values()):
            continue
        rows.append({"what": what, "note": note, "strong": strong, "by": by})
    return rows
