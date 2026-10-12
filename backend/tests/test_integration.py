import asyncio
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import AsyncMock
from sqlalchemy import func, select
from app.api import crypto
from app.models import GitHubConnection, GitHubRepository, Notification, Project, Task, TimeEntry, User, utcnow
from app import service as svc
from app.schemas import TaskCreate, TaskEdit
from app.worker import deliver_one, schedule_deadlines
from conftest import signed_identity
import pytest


async def join_member(client, project, telegram_id=900000002):
    invitation = (await client.post(f'/api/projects/{project["id"]}/invites', json={'target': str(telegram_id)})).json()
    header = {'Authorization': signed_identity(telegram_id, 'test_member')}
    accepted = await client.post('/api/invites/' + invitation['token'] + '/accept', headers=header)
    assert accepted.status_code == 200, accepted.text
    return header


async def test_task_create_edit_conflict_delete_restore(client, project, task):
    assert task['key'] == 'ALT-1' and task['priority'] == 'Medium'
    edit = await client.patch(f'/api/tasks/{task["id"]}', json={'version': 1, 'title': 'Updated', 'deadline': '2026-10-20'})
    assert edit.status_code == 200 and edit.json()['version'] == 2
    assert (await client.patch(f'/api/tasks/{task["id"]}', json={'version': 1, 'title': 'Lost edit'})).status_code == 409
    deleted = (await client.patch(f'/api/tasks/{task["id"]}', json={'version': 2, 'deleted': True})).json()
    assert deleted['deleted_at']
    assert not (await client.get(f'/api/projects/{project["id"]}/tasks')).json()
    assert len((await client.get(f'/api/projects/{project["id"]}/tasks?deleted=true')).json()) == 1
    restored = await client.patch(f'/api/tasks/{task["id"]}', json={'version': 3, 'deleted': False})
    assert restored.status_code == 200 and restored.json()['deleted_at'] is None
    assert len((await client.get(f'/api/tasks/{task["id"]}')).json()['activity']) == 4


async def test_access_control_all_resources(client, project, task):
    attacker = {'Authorization': signed_identity(900000003, 'outsider')}
    for path in [f'/api/projects/{project["id"]}', f'/api/tasks/{task["id"]}', f'/api/projects/{project["id"]}/tasks', f'/api/projects/{project["id"]}/activity', f'/api/projects/{project["id"]}/members', f'/api/projects/{project["id"]}/sprints', f'/api/projects/{project["id"]}/repositories']:
        assert (await client.get(path, headers=attacker)).status_code == 404
    assert (await client.patch(f'/api/tasks/{task["id"]}', json={'version': 1, 'title': 'Attack'}, headers=attacker)).status_code == 404
    assert (await client.get('/api/projects', headers=attacker)).json() == []


async def test_auth_required(client):
    assert (await client.get('/api/projects', headers={'Authorization': ''})).status_code == 401
    assert (await client.get('/api/projects', headers={'Authorization': signed_identity(age=7200)})).status_code == 401


async def test_invitation_target_replay_expiry(client, project, database):
    invitation = (await client.post(f'/api/projects/{project["id"]}/invites', json={'target': '@test_member'})).json()
    path = '/api/invites/' + invitation['token'] + '/accept'
    assert (await client.post(path)).status_code == 403
    member = {'Authorization': signed_identity(900000002, 'test_member')}
    assert (await client.post(path, headers=member)).status_code == 200
    assert (await client.post(path, headers=member)).status_code == 404
    expiring = (await client.post(f'/api/projects/{project["id"]}/invites', json={})).json()
    from app.models import Invitation
    async with database.begin() as db:
        item = await db.scalar(select(Invitation).where(Invitation.token_hash == hashlib.sha256(expiring['token'].encode()).hexdigest()))
        item.expires_at = utcnow() - timedelta(seconds=1)
    assert (await client.post('/api/invites/' + expiring['token'] + '/accept', headers=member)).status_code == 404


async def test_project_owner_deletion(client, project):
    member = await join_member(client, project)
    assert (await client.delete(f'/api/projects/{project["id"]}', headers=member, params={})).status_code == 422
    response = await client.request('DELETE', f'/api/projects/{project["id"]}', headers=member, json={'confirm_key': 'ALT'})
    assert response.status_code == 403
    assert (await client.request('DELETE', f'/api/projects/{project["id"]}', json={'confirm_key': 'WRONG'})).status_code == 422
    assert (await client.request('DELETE', f'/api/projects/{project["id"]}', json={'confirm_key': 'ALT'})).status_code == 200
    assert (await client.get(f'/api/projects/{project["id"]}')).status_code == 404


async def test_status_configuration_and_cross_project(client, project, task):
    status = (await client.post(f'/api/projects/{project["id"]}/statuses', json={'name': 'Released', 'is_done': True})).json()
    assert status['is_done']
    assert (await client.patch(f'/api/projects/{project["id"]}/statuses/{status["id"]}', json={'name': 'Shipped', 'is_done': True})).status_code == 200
    statuses = (await client.get(f'/api/projects/{project["id"]}/statuses')).json()
    ids = [s['id'] for s in statuses][::-1]
    assert (await client.put(f'/api/projects/{project["id"]}/status-order', json={'ids': ids})).status_code == 200
    assert [s['id'] for s in (await client.get(f'/api/projects/{project["id"]}/statuses')).json()] == ids
    assert (await client.put(f'/api/projects/{project["id"]}/status-order', json={'ids': [ids[0]]})).status_code == 422
    other = (await client.post('/api/projects', json={'name': 'Other', 'key': 'OTH'})).json()
    foreign = (await client.get(f'/api/projects/{other["id"]}')).json()['statuses'][0]['id']
    assert (await client.patch(f'/api/tasks/{task["id"]}', json={'version': 1, 'status_id': foreign})).status_code == 422


async def test_sprint_lifecycle(client, project, task):
    assert (await client.post(f'/api/projects/{project["id"]}/sprints', json={'name': 'Wrong', 'start_date': '2026-10-20', 'end_date': '2026-10-10'})).status_code == 422
    sprint = (await client.post(f'/api/projects/{project["id"]}/sprints', json={'name': 'Sprint 1', 'start_date': '2026-10-12', 'end_date': '2026-10-26'})).json()
    assert (await client.patch(f'/api/tasks/{task["id"]}', json={'version': 1, 'sprint_id': sprint['id'], 'status_id': project['statuses'][-1]['id']})).status_code == 200
    url = f'/api/projects/{project["id"]}/sprints/{sprint["id"]}'
    assert (await client.patch(url, json={'state': 'active'})).status_code == 200
    result = (await client.patch(url, json={'state': 'completed'})).json()
    assert result['result']['total'] == 1 and result['result']['done'] == 1
    assert (await client.patch(url, json={'state': 'active'})).status_code == 409


async def test_timer_pause_stop_restart(client, project, task, database):
    response = await client.post(f'/api/timer/{task["id"]}/start')
    assert response.status_code == 200, response.text
    async with database.begin() as db:
        entry = await db.scalar(select(TimeEntry).where(TimeEntry.ended_at.is_(None)))
        entry.running_since = utcnow() - timedelta(seconds=125)
    # The next request opens another DB session, simulating process recovery.
    assert (await client.get('/api/timer')).json()['elapsed_seconds'] >= 125
    paused = (await client.post('/api/timer/pause')).json()
    assert paused['seconds'] >= 125 and paused['running_since'] is None
    assert (await client.post(f'/api/timer/{task["id"]}/start')).status_code == 200
    stopped = (await client.post('/api/timer/stop')).json()
    assert stopped['ended_at'] and stopped['seconds'] >= 125
    assert (await client.get('/api/timer')).json() is None
    assert (await client.post(f'/api/tasks/{task["id"]}/time', json={'seconds': 600, 'note': 'Manual'})).status_code == 200
    assert (await client.get(f'/api/tasks/{task["id"]}')).json()['total_seconds'] >= 725


async def test_conflicting_timers(client, project, task):
    other = (await client.post(f'/api/projects/{project["id"]}/tasks', json={'title': 'Other'})).json()
    assert (await client.post(f'/api/timer/{task["id"]}/start')).status_code == 200
    assert (await client.post(f'/api/timer/{other["id"]}/start')).status_code == 409


async def test_checklist_comments_attachments(client, project, task):
    item = (await client.post(f'/api/tasks/{task["id"]}/checklist', json={'text': 'Add tests'})).json()
    assert (await client.patch(f'/api/tasks/{task["id"]}/checklist/{item["id"]}', json={'done': True})).json()['done']
    assert (await client.post(f'/api/tasks/{task["id"]}/comments', json={'text': 'Ready'})).status_code == 201
    attachment = await client.post(f'/api/tasks/{task["id"]}/attachments', files={'file': ('notes.txt', b'hello', 'text/plain')})
    assert attachment.status_code == 201, attachment.text
    assert 'storage_key' not in attachment.json()
    assert (await client.get(f'/api/attachments/{attachment.json()["id"]}')).content == b'hello'
    attacker = {'Authorization': signed_identity(900000003, 'outsider')}
    assert (await client.get(f'/api/attachments/{attachment.json()["id"]}', headers=attacker)).status_code == 404
    assert (await client.post(f'/api/tasks/{task["id"]}/attachments', files={'file': ('evil.svg', b'<script>', 'image/svg+xml')})).status_code == 422
    assert (await client.delete(f'/api/tasks/{task["id"]}/checklist/{item["id"]}')).status_code == 200


async def test_notifications_dedupe_preferences_restart(client, project, task, database):
    member = await join_member(client, project)
    await client.post(f'/api/tasks/{task["id"]}/comments', json={'text': 'Notification'})
    async with database.begin() as db:
        notifications = (await db.scalars(select(Notification))).all()
        assert len(notifications) == 1
        assert notifications[0].user_id != project['owner_id']
    bot = AsyncMock()
    assert await deliver_one(bot)
    assert bot.send_message.await_count == 1
    assert not await deliver_one(bot)
    await client.put(f'/api/projects/{project["id"]}/notifications', headers=member, json={'events': {'comment': False}})
    await client.post(f'/api/tasks/{task["id"]}/comments', json={'text': 'Muted'})
    assert not await deliver_one(bot)
    await client.patch(f'/api/tasks/{task["id"]}', json={'version': 1, 'deadline': svc.today().isoformat()})
    await schedule_deadlines(); await schedule_deadlines()
    async with database.begin() as db:
        count = await db.scalar(select(func.count()).select_from(Notification).where(Notification.dedupe_key.like('deadline:%')))
        assert count == 2


async def test_notification_retry_survives_new_session(client, project, task, database):
    await join_member(client, project)
    await client.post(f'/api/tasks/{task["id"]}/comments', json={'text': 'Retry'})
    bot = AsyncMock(); bot.send_message.side_effect = RuntimeError('network')
    await deliver_one(bot)
    async with database.begin() as db:
        entry = await db.scalar(select(Notification))
        assert entry.attempts == 1 and not entry.sent_at
        entry.available_at = utcnow() - timedelta(seconds=1)
    bot.send_message.side_effect = None
    await deliver_one(bot)
    assert bot.send_message.await_count == 2


@pytest.mark.parametrize('outcome', ['forbidden', 'rate_limit'])
async def test_notification_telegram_delivery_conditions(client, project, task, database, outcome):
    from app.worker import DeliveryForbidden, DeliveryRetry
    await join_member(client, project)
    await client.post(f'/api/tasks/{task["id"]}/comments', json={'text': 'Delivery condition'})
    bot = AsyncMock()
    bot.send_message.side_effect = DeliveryForbidden() if outcome == 'forbidden' else DeliveryRetry(120)
    await deliver_one(bot)
    async with database.begin() as db:
        entry = await db.scalar(select(Notification))
        assert not entry.sent_at
        if outcome == 'forbidden':
            assert entry.failed
        else:
            assert not entry.failed and entry.available_at > utcnow() + timedelta(seconds=100)


async def test_github_webhook_signature_replay_scope(client, project, task, database):
    async with database.begin() as db:
        connection = GitHubConnection(project_id=project['id'], user_id=project['owner_id'])
        db.add(connection); await db.flush()
        repo = GitHubRepository(project_id=project['id'], connection_id=connection.id, full_name='owner/repo', encrypted_secret=crypto.encrypt(b'test-secret').decode())
        db.add(repo); await db.flush(); repo_id = repo.id
    payload = {'repository': {'full_name': 'owner/repo'}, 'commits': [{'message': 'fix(ALT-1): auth', 'url': 'https://github.com/owner/repo/commit/' + 'a' * 40}]}
    raw = json.dumps(payload).encode(); signature = 'sha256=' + hmac.new(b'test-secret', raw, hashlib.sha256).hexdigest()
    headers = {'X-Hub-Signature-256': signature, 'X-GitHub-Delivery': 'delivery-1', 'X-GitHub-Event': 'push', 'Content-Type': 'application/json'}
    path = f'/api/github/webhook/{repo_id}'
    assert (await client.post(path, content=raw, headers={**headers, 'X-Hub-Signature-256': 'bad'})).status_code == 401
    response = await client.post(path, content=raw, headers=headers)
    assert response.status_code == 200 and response.json()['linked'] == 1, response.text
    assert (await client.post(path, content=raw, headers=headers)).json()['duplicate']
    details = (await client.get(f'/api/tasks/{task["id"]}')).json()
    assert len(details['github']) == 1 and details['status_id'] == task['status_id']


async def test_concurrent_counter_and_edits(client, project, task, database):
    async def create_one(title):
        async with database.begin() as db:
            created = await svc.create_task(db, project['owner_id'], project['id'], TaskCreate(title=title))
            return created.key
    keys = await asyncio.gather(create_one('One'), create_one('Two'))
    assert len(set(keys)) == 2 and set(keys) == {'ALT-2', 'ALT-3'}
    async def update(title):
        from fastapi import HTTPException
        try:
            async with database.begin() as db:
                await svc.update_task(db, project['owner_id'], task['id'], TaskEdit(version=1, title=title))
            return 200
        except HTTPException as error:
            return error.status_code
    assert sorted(await asyncio.gather(update('A'), update('B'))) == [200, 409]
