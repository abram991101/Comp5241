import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from flask import Flask

from src.models.note import db
from src.routes.note import note_bp
from src.routes.translate import translate_bp
from src.translator import TranslationError, translate


def provider_response(text='你好', language='zh', **overrides):
    choice = {'finish_reason': 'stop', 'message': {'content': json.dumps({
        'translated_text': text, 'target_language': language})}}
    choice.update(overrides)
    return io.BytesIO(json.dumps({'choices': [choice]}).encode())


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {
            'LLM_API_KEY': 'test-only-key',
            'LLM_BASE_URL': 'https://genai.comp.polyu.edu.hk/api/v1',
            'LLM_MODEL': 'DeepSeek-V4-Flash',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
        self.app.register_blueprint(note_bp, url_prefix='/api')
        self.app.register_blueprint(translate_bp, url_prefix='/api')
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()
        self.client = self.app.test_client()

    def test_both_directions_and_request(self):
        for source, target, expected in [('Hello', 'zh', '你好'), ('你好', 'en', 'Hello')]:
            with self.subTest(target=target), patch('src.translator.urlopen', return_value=provider_response(expected, target)) as call:
                response = self.client.post('/api/translate', json={'text': source, 'target_language': target})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, {'translated_text': expected, 'target_language': target})
                request = call.call_args.args[0]
                self.assertEqual(request.full_url, 'https://genai.comp.polyu.edu.hk/api/v1/chat/completions')
                self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-key')
                self.assertEqual(json.loads(request.data)['model'], 'DeepSeek-V4-Flash')
                self.assertEqual(call.call_args.kwargs['timeout'], 60)

    def test_full_endpoint_supported(self):
        with patch.dict('os.environ', {'LLM_BASE_URL': 'https://genai.comp.polyu.edu.hk/api/v1/chat/completions'}), patch('src.translator.urlopen', return_value=provider_response()) as call:
            translate('Hello', 'zh')
            self.assertEqual(call.call_args.args[0].full_url.count('/chat/completions'), 1)

    def test_invalid_inputs_do_not_call_provider(self):
        for body in [None, [], {}, {'text': '', 'target_language': 'zh'}, {'text': 123, 'target_language': 'en'}, {'text': 'a' * 12001, 'target_language': 'zh'}, {'text': 'Hello', 'target_language': 'fr'}, {'text': 'Hello', 'target_language': []}]:
            with self.subTest(body=str(body)[:60]), patch('src.translator.urlopen') as call:
                self.assertEqual(self.client.post('/api/translate', json=body).status_code, 400)
                call.assert_not_called()
        self.assertEqual(self.client.post('/api/translate', data='{', content_type='application/json').status_code, 400)

    def test_configuration(self):
        for config in [{'LLM_API_KEY': ''}, {'LLM_BASE_URL': 'http://example.com'}, {'LLM_MODEL': ''}]:
            with patch.dict('os.environ', config), patch('src.translator.urlopen') as call:
                self.assertEqual(self.client.post('/api/translate', json={'text': 'Hello', 'target_language': 'zh'}).status_code, 503)
                call.assert_not_called()

    def test_provider_failures_are_safe_json(self):
        for error, status, code in [
            (TimeoutError(), 504, 'timeout'),
            (URLError(TimeoutError()), 504, 'timeout'),
            (URLError('test-only-key'), 502, 'connection_error'),
            (HTTPError('url', 401, 'test-only-key', {}, None), 502, 'authentication_failed'),
            (HTTPError('url', 403, 'test-only-key', {}, None), 502, 'authentication_failed'),
            (HTTPError('url', 429, 'test-only-key', {}, None), 502, 'provider_error'),
            (HTTPError('url', 500, 'test-only-key', {}, None), 502, 'provider_error'),
        ]:
            with self.subTest(code=code), patch('src.translator.urlopen', side_effect=error):
                response = self.client.post('/api/translate', json={'text': 'Hello', 'target_language': 'zh'})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json['code'], code)
                self.assertNotIn('test-only-key', response.get_data(as_text=True))

    def test_invalid_provider_json(self):
        responses = [io.BytesIO(b'not JSON'), io.BytesIO(b'{}'), io.BytesIO(b'{"choices": []}'),
                     provider_response(message={'content': '```json\n{}\n```'}),
                     provider_response(message={'content': '[]'}),
                     provider_response(language='en'), provider_response(text=''),
                     provider_response(finish_reason='length')]
        for response in responses:
            with patch('src.translator.urlopen', return_value=response):
                result = self.client.post('/api/translate', json={'text': 'Hello', 'target_language': 'zh'})
                self.assertEqual(result.status_code, 502)
                self.assertEqual(result.json['code'], 'invalid_response')

    def test_note_crud_search_and_translation_preserves_original(self):
        original = {'title': 'Original title', 'content': 'Hello world'}
        created = self.client.post('/api/notes', json=original)
        self.assertEqual(created.status_code, 201)
        url = '/api/notes/' + str(created.json['id'])
        self.assertEqual(self.client.get(url).json, created.json)
        self.assertEqual(len(self.client.get('/api/notes').json), 1)
        self.assertEqual(len(self.client.get('/api/notes/search?q=Hello').json), 1)
        with patch('src.translator.urlopen', return_value=provider_response()):
            self.assertEqual(self.client.post('/api/translate', json={'text': original['content'], 'target_language': 'zh'}).status_code, 200)
        self.assertEqual(self.client.get(url).json, created.json)
        updated = self.client.put(url, json={'content': 'Updated content'})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.get(url).json['content'], 'Updated content')
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get('/api/notes').json, [])


if __name__ == '__main__':
    unittest.main()
