"""テストの土台。**DBはインメモリ**。本番のファイルに触らない。"""
import pytest

from app import create_app
from app.config import Config
from app.extensions import db as _db
from app.models import User


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False          # テストでは外す（本番は必ず有効）
    SECRET_KEY = "test"
    CREDENTIAL_KEY = ""
    BASE_URL = "http://localhost"
    ORG_NAME = "テスト組織"


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        _db.create_all()
        # **テストでは全機能を公開にする。**
        # 公開の出し分けそのものは test_features.py で確かめる。
        # ここを閉じたままにすると、機能のテストが「404だから通らない」で
        # 落ちて、**本当の不具合と見分けがつかなくなる**
        from app.models import Feature
        for f in Feature.all_rows():
            f.released = True
        _db.session.commit()
    yield app
    with app.app_context():
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield _db


@pytest.fixture()
def client(app):
    return app.test_client()


def _login(app, client, role, email=None):
    email = email or f"{role}@test"
    with app.app_context():
        u = User(email=email, name=role, role=role)
        u.set_password("password-for-test")
        _db.session.add(u)
        _db.session.commit()
        uid = u.id
    client.post("/login", data={"email": email, "password": "password-for-test"})
    return uid


@pytest.fixture()
def as_owner(app, client):
    _login(app, client, "owner")
    return client


@pytest.fixture()
def as_staff(app, client):
    _login(app, client, "staff")
    return client


@pytest.fixture()
def as_master(app, client):
    """当社（開発側）。**作りかけの機能も見える。**"""
    _login(app, client, "master", email="master@test")
    return client
