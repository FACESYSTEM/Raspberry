"""F-SERVICE 撮影端末の Raspberry Pi 版。

端末の仕事は「全コマを撮る → 動きのないコマを間引く → 残りをサーバへ送る」だけ。
検出・追跡・照合・束ねはすべてサーバが行う。
"""

VERSION = "pi-0.1.0"
# サーバの version_code（整数）。版を上げるたびに 1 つ増やす
VERSION_CODE = 1
