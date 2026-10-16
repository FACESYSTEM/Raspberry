"""修正モード。**画面を見ている人が、その場で指摘できるようにする。**

使い方（3行）::

    from fixmode import FixMode
    fix = FixMode(db)
    fix.init_app(app, can_comment=..., can_close=..., can_fix=..., who=...)

土台側の約束ごと：

* `base.html` に ``{% include "fixmode/widget.html" %}``（本文の先頭）
* `base.html` の script ブロックに ``{% include "fixmode/script.html" %}``
* `<head>` に ``<meta name="csrf-token" content="...">``（CSRFを使う場合）
* サイドバーなどに ``{% include "fixmode/switch.html" %}``

**この仕掛けは利用者の表を知らない。**名前は書いた時点で写して持つ。
他のシステムへ持っていくときに、利用者の作りが違っても動くようにするため。
"""
from .core import STATE_LABEL, FixMode

__all__ = ["FixMode", "STATE_LABEL"]
__version__ = "1.0.1"
