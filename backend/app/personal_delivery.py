from datetime import timedelta
from sqlalchemy import select, or_
from .models import PersonalTask, PersonalReminder, User, utcnow
from .db import Session


async def deliver_personal(bot):
    from .worker import DeliveryForbidden, DeliveryRetry
    async with Session.begin() as db:
        now = utcnow()
        task = await db.scalar(select(PersonalTask).join(PersonalReminder, PersonalReminder.task_id == PersonalTask.id).where(
            PersonalTask.done_at.is_(None), PersonalTask.deleted_at.is_(None), PersonalReminder.enabled.is_(True),
            PersonalReminder.next_at <= now, or_(PersonalReminder.retry_at.is_(None), PersonalReminder.retry_at <= now)
        ).order_by(PersonalReminder.next_at).with_for_update(of=PersonalTask, skip_locked=True).limit(1))
        if not task:
            return False
        schedule = await db.scalar(select(PersonalReminder).where(PersonalReminder.task_id == task.id).with_for_update())
        user = await db.get(User, task.user_id)
        from .personal import due_label
        markup = {'inline_keyboard': [[{'text': 'Готово', 'callback_data': f'pdone:{task.id}'},
                                       {'text': 'Через час', 'callback_data': f'psnooze:{task.id}'}],
                                      [{'text': 'Не напоминать', 'callback_data': f'pmute:{task.id}'}]]}
        try:
            await bot.send_message(user.telegram_id, f'Напоминаю\n\n{task.title}\nСрок: {due_label(task)}', reply_markup=markup)
            schedule.last_sent_at = now
            schedule.attempts, schedule.retry_at = 0, None
            if schedule.interval_seconds:
                steps = int((now - schedule.next_at).total_seconds()) // schedule.interval_seconds + 1
                schedule.next_at += timedelta(seconds=steps * schedule.interval_seconds)
            else:
                schedule.enabled = False
        except DeliveryForbidden:
            schedule.enabled = False
        except DeliveryRetry as error:
            schedule.retry_at = now + timedelta(seconds=error.retry_after + 1)
        except Exception:
            schedule.attempts += 1
            schedule.retry_at = now + timedelta(seconds=min(3600, 10 * 2 ** schedule.attempts))
            if schedule.attempts >= 12:
                schedule.enabled = False
        return True
