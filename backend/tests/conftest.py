import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
import httpx
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.config import settings
from app.models import Base
from app.db import session
from app.api import app


def signed_identity(telegram_id=900000001, username='test_owner', age=0):
    values = {'auth_date': str(int(time.time()) - age), 'user': json.dumps({'id': telegram_id, 'username': username, 'first_name': username}, separators=(',', ':'))}
    check = '\n'.join(f'{k}={v}' for k, v in sorted(values.items()))
    secret = hmac.new(b'WebAppData', settings().bot_token.encode(), hashlib.sha256).digest()
    values['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return 'tma ' + urlencode(values)


@pytest_asyncio.fixture
async def database(monkeypatch):
    url = os.environ.get('TEST_DATABASE_URL', '')
    if not url or not url.rsplit('/', 1)[-1].endswith('_test'):
        raise RuntimeError('Tests require a dedicated database name ending in _test')
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def override():
        async with factory.begin() as db:
            yield db
    app.dependency_overrides[session] = override
    import app.api as api_module
    import app.worker as worker_module
    redis = Redis.from_url(settings().redis_url)
    monkeypatch.setattr(api_module, 'redis', redis)
    monkeypatch.setattr(worker_module, 'Session', factory)
    yield factory
    app.dependency_overrides.clear()
    await redis.aclose()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(database):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization': signed_identity()}) as client:
        yield client


@pytest_asyncio.fixture
async def project(client):
    response = await client.post('/api/projects', json={'name': 'Altair', 'key': 'ALT'})
    assert response.status_code == 201, response.text
    return (await client.get('/api/projects/' + str(response.json()['id']))).json()


@pytest_asyncio.fixture
async def task(client, project):
    response = await client.post(f'/api/projects/{project["id"]}/tasks', json={'title': 'Fix auth'})
    assert response.status_code == 201, response.text
    return response.json()
