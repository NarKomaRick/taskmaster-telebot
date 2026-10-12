import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from fastapi import HTTPException


def validate_init_data(raw: str, bot_token: str, max_age: int = 3600) -> dict:
    try:
        pairs = parse_qsl(raw, strict_parsing=True)
        values = dict(pairs)
        if len(pairs) != len(values):
            raise ValueError('duplicate fields')
        received = values.pop('hash')
        check = '\n'.join(f'{key}={value}' for key, value in sorted(values.items()))
        secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise ValueError('signature')
        age = time.time() - int(values['auth_date'])
        if age < -30 or age > max_age:
            raise ValueError('expired')
        user = json.loads(values['user'])
        if not isinstance(user, dict):
            raise ValueError('user')
        if not isinstance(user.get('id'), int) or isinstance(user['id'], bool) or user['id'] <= 0:
            raise ValueError('user')
        return user
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, 'Откройте приложение заново через Telegram') from None


def verify_github(raw: bytes, signature: str, secret: str):
    expected = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise HTTPException(401, 'Неверная подпись GitHub')
