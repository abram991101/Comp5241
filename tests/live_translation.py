"""Explicit opt-in real provider check: python -m tests.live_translation."""
import json

from flask import Flask
from src.routes.translate import translate_bp


def main():
    app = Flask(__name__)
    app.register_blueprint(translate_bp, url_prefix='/api')
    with app.test_client() as client:
        for text, language in [('Hello, world!', 'zh'), ('今天我们学习软件工程。', 'en')]:
            response = client.post('/api/translate', json={'text': text, 'target_language': language})
            print(json.dumps({'status': response.status_code, 'result': response.json}, ensure_ascii=True))
            if response.status_code != 200:
                raise SystemExit(1)


if __name__ == '__main__':
    main()
