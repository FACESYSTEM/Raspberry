"""設定の読み込み。**設定したつもりで効いていない、を防ぐ。**

`.env` に鍵を書いても読まれていなければ、既定値のまま動く。
書いた側は設定できたと思っているので、**誰も気づけない**。
"""
import re
from pathlib import Path

import pytest

from app import create_app
from app.config import BASE_DIR, INSECURE_SECRET_KEY, Config


class Test環境変数の読み込み:
    def test_configがdotenvを読んでいる(self):
        """`load_dotenv` を消すと `.env` が丸ごと効かなくなる。ここで固定する。"""
        src = (BASE_DIR / "app" / "config.py").read_text(encoding="utf-8")
        assert "load_dotenv(" in src

    def test_envの例にある項目はすべて読まれている(self):
        """`.env.example` に足したのに `config.py` で読み忘れる、を防ぐ。"""
        example = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
        src = (BASE_DIR / "app" / "config.py").read_text(encoding="utf-8")
        keys = re.findall(r"^([A-Z_]+)=", example, flags=re.M)
        assert keys, ".env.example が読めていない"
        missing = [k for k in keys if k not in src]
        assert not missing, f"config.py が読んでいない項目: {missing}"


class Test鍵のガード:
    def test_既定値のままなら起動しない(self):
        """起動できてしまうと、世に出回っている鍵で署名されたまま動き続ける。"""
        class Bad(Config):
            TESTING = False
            SECRET_KEY = INSECURE_SECRET_KEY
        with pytest.raises(RuntimeError) as e:
            create_app(Bad)
        assert "SECRET_KEY" in str(e.value)

    def test_止めるときは直し方を示す(self):
        """「駄目です」だけのメッセージは、受け取った人が動けない。"""
        class Bad(Config):
            TESTING = False
            SECRET_KEY = INSECURE_SECRET_KEY
        with pytest.raises(RuntimeError) as e:
            create_app(Bad)
        assert ".env" in str(e.value) and "secrets" in str(e.value)

    def test_設定してあれば起動する(self):
        class Good(Config):
            TESTING = False
            SECRET_KEY = "設定された鍵"
            SQLALCHEMY_DATABASE_URI = "sqlite://"
        assert create_app(Good) is not None
