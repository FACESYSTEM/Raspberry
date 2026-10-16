"""開発用の起動。本番は gunicorn を使う（README参照）。"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
