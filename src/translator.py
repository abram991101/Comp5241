"""Server-only PolyU translation client; never persists note content."""

import json
import os
import socket
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
MAX_TEXT_LENGTH = 12000


class TranslationError(Exception):
    def __init__(self, message, code, status):
        super().__init__(message)
        self.code = code
        self.status = status


def translate(text, target_language):
    if not isinstance(text, str) or not text.strip():
        raise TranslationError('Please enter text to translate.', 'invalid_input', 400)
    if len(text) > MAX_TEXT_LENGTH:
        raise TranslationError('Text must be at most 12000 characters.', 'invalid_input', 400)
    if target_language not in ('en', 'zh'):
        raise TranslationError('Target language must be en or zh.', 'invalid_input', 400)

    key = os.getenv('LLM_API_KEY', '').strip()
    base = os.getenv('LLM_BASE_URL', 'https://genai.comp.polyu.edu.hk/api/v1').strip().rstrip('/')
    model = os.getenv('LLM_MODEL', 'DeepSeek-V4-Flash').strip()
    if not key or not base or not model:
        raise TranslationError('Translation service is not configured.', 'configuration_error', 503)
    endpoint = base if base.endswith('/chat/completions') else base + '/chat/completions'
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TranslationError('Translation service URL is invalid.', 'configuration_error', 503)
    try:
        prompt = (ROOT / 'prompts' / 'translate_prompt.md').read_text(encoding='utf-8')
    except OSError:
        raise TranslationError('Translation prompt is unavailable.', 'configuration_error', 503) from None
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': json.dumps({'text': text, 'target_language': target_language}, ensure_ascii=False)},
        ],
        'response_format': {'type': 'json_object'},
        'max_tokens': 8192,
    }
    request = Request(endpoint, data=json.dumps(payload).encode('utf-8'), headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
    }, method='POST')
    try:
        with urlopen(request, timeout=60) as response:
            envelope = json.loads(response.read().decode('utf-8'))
        choice = envelope['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('Incomplete completion')
        result = json.loads(choice['message']['content'])
        if (not isinstance(result, dict)
                or result.get('target_language') != target_language
                or not isinstance(result.get('translated_text'), str)
                or not result['translated_text'].strip()):
            raise ValueError('Invalid translation schema')
        return {'translated_text': result['translated_text'], 'target_language': target_language}
    except HTTPError as error:
        if error.code in (401, 403):
            raise TranslationError('Translation authentication failed. Check the server API key.', 'authentication_failed', 502) from None
        raise TranslationError('Translation provider is unavailable. Please try again.', 'provider_error', 502) from None
    except (TimeoutError, socket.timeout):
        raise TranslationError('Translation timed out. Please try again.', 'timeout', 504) from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise TranslationError('Translation timed out. Please try again.', 'timeout', 504) from None
        raise TranslationError('Cannot connect to the translation provider.', 'connection_error', 502) from None
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        raise TranslationError('Translation provider returned invalid or incomplete JSON.', 'invalid_response', 502) from None
