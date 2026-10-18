import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { Bell, Check, ChevronRight, LoaderCircle, Mic, Plus, X } from 'lucide-react';
import { api, queryClient } from './api';
import './style.css';

type Reminder = { enabled: boolean; next_at: string; interval_seconds: number; attempts: number };
type Todo = { id: number; title: string; note: string; due_at: string | null; due_has_time: boolean; bucket: string; done_at: string | null; version: number; reminder: Reminder | null };
const timezone = 'Europe/Moscow';
const frequencies: Record<number, string> = { 0: 'Один раз', 3600: 'Каждый час', 86400: 'Каждый день', 259200: 'Каждые 3 дня', 604800: 'Каждую неделю' };
const dayKey = (value: string | Date) => new Intl.DateTimeFormat('sv-SE', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
const labelDate = (value: string, hasTime = false) => new Intl.DateTimeFormat('ru', { timeZone: timezone, day: 'numeric', month: 'long', ...(hasTime ? { hour: '2-digit', minute: '2-digit' } : {}) }).format(new Date(value));
const localInput = (value: string) => dayKey(value) + 'T' + new Intl.DateTimeFormat('en-GB', { timeZone: timezone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
const isToday = (t: Todo) => t.bucket === 'today' || Boolean(t.due_at && dayKey(t.due_at) <= dayKey(new Date()));
const belongs = (t: Todo, tab: string) => tab === 'today' ? isToday(t) : !isToday(t) && t.bucket === tab;

function App() {
  const [tab, setTab] = useState('inbox');
  const [completed, setCompleted] = useState(false);
  const [text, setText] = useState('');
  const [selected, setSelected] = useState<Todo | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const loggedIn = Boolean(window.Telegram?.WebApp.initData);
  const tasks = useQuery({ queryKey: ['personal'], queryFn: () => api<Todo[]>('/personal/tasks'), enabled: loggedIn });
  useEffect(() => { if (!notice) return; const id = setTimeout(() => setNotice(''), 4000); return () => clearTimeout(id); }, [notice]);
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    tg?.ready(); tg?.expand();
    const theme = () => { document.documentElement.dataset.theme = tg?.colorScheme || 'light'; };
    theme(); tg?.onEvent('themeChanged', theme);
    return () => tg?.offEvent('themeChanged', theme);
  }, []);
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    const back = () => { setSelected(null); setCompleted(false); };
    if (selected || completed) tg?.BackButton.show(); else tg?.BackButton.hide();
    tg?.BackButton.onClick(back);
    return () => tg?.BackButton.offClick(back);
  }, [selected, completed]);
  const act = async (fn: () => Promise<unknown>, after?: () => void) => {
    if (pending) return;
    setPending(true); setError('');
    try { await fn(); await queryClient.invalidateQueries({ queryKey: ['personal'] }); after?.(); }
    catch (e) { setError((e as Error).message); await queryClient.invalidateQueries({ queryKey: ['personal'] }); }
    finally { setPending(false); }
  };
  const open = (id: number) => { const task = tasks.data?.find(t => t.id === id); if (task) { setError(''); setSelected(task); } };
  const active = tasks.data?.filter(t => !t.done_at) || [];
  const visible = completed ? tasks.data?.filter(t => t.done_at) || [] : active.filter(t => belongs(t, tab));
  if (!loggedIn) return <main className="welcome"><div className="logo"><Check size={30}/></div><h1>Мои дела</h1><p>Напишите или скажите, что нужно сделать.<br/>Остальное сохранит бот.</p><a className="primary" href="https://t.me/taskovio_bot">Открыть бота</a><small>Вход через Telegram</small></main>;
  return <div className="app">
    <header><div><span className="eyebrow">ЛИЧНЫЙ ДИСПЕТЧЕР</span><h1>{completed ? 'Выполненные' : 'Мои дела'}</h1></div><button className="quiet" onClick={() => setCompleted(!completed)}>{completed ? 'К делам' : 'Выполненные'}</button></header>
    {!completed ? <nav className="tabs" aria-label="Списки дел">{[['inbox','Входящие'],['today','Сегодня'],['later','Позже']].map(([key,label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => { setTab(key); setNotice(''); }}>{label}<span>{active.filter(t => belongs(t, key)).length}</span></button>)}</nav> : null}
    <main>
      {error || tasks.error ? <div className="error" role="alert">{error || (tasks.error as Error).message}<button onClick={() => { setError(''); void tasks.refetch(); }} aria-label="Повторить"><X size={16}/></button></div> : null}
      {notice ? <div className="notice" role="status">{notice}</div> : null}
      <p className="section-note">{completed ? 'То, что уже сделано.' : { inbox: 'Всё, что хочется не забыть.', today: 'Ближайшее и важное на сегодня.', later: 'Сохранено. Вернётесь, когда придёт время.' }[tab]}</p>
      {tasks.isPending ? <div className="empty"><LoaderCircle className="spin"/>Загрузка...</div> : !visible.length ? <div className="empty"><div className="empty-icon"><Check size={26}/></div><h2>{completed ? 'Пока нет выполненных дел' : 'Здесь пока пусто'}</h2><p>Добавьте дело внизу или отправьте<br/>голосовое в чат с ботом.</p></div> : <div className="task-list">{[...visible].sort((a,b) => (a.due_at || '9999').localeCompare(b.due_at || '9999')).map(t => <article className={'todo ' + (t.done_at ? 'done' : '')} key={t.id}>
        <button className="complete" aria-label={(t.done_at ? 'Вернуть: ' : 'Выполнить: ') + t.title} disabled={pending} onClick={() => void act(() => api(`/personal/tasks/${t.id}`, 'PATCH', { version: t.version, done: !t.done_at }))}>{t.done_at ? <Check size={15}/> : null}</button>
        <button className="todo-body" onClick={() => open(t.id)}><strong>{t.title}</strong>{t.due_at ? <span className={new Date(t.due_at) < new Date() && !t.done_at ? 'late' : ''}>До {labelDate(t.due_at, t.due_has_time)}</span> : null}{t.reminder?.enabled ? <small><Bell size={11}/>{labelDate(t.reminder.next_at, true)} · {frequencies[t.reminder.interval_seconds].toLowerCase()}</small> : null}{t.reminder && !t.reminder.enabled && t.reminder.attempts >= 12 ? <small className="late">Не удалось доставить напоминание. Настройте заново.</small> : null}</button><ChevronRight className="chevron" size={15}/>
      </article>)}</div>}
    </main>
    <footer><form className="composer" onSubmit={e => { e.preventDefault(); if (!text.trim()) return; void act(async () => { const task = await api<Todo>('/personal/tasks', 'POST', { text }); setText(''); setTab(isToday(task) ? 'today' : task.bucket); setCompleted(false); setSelected(task); setNotice('Дело записано. Можно добавить напоминание.'); }); }}><input value={text} onChange={e => setText(e.target.value)} maxLength={4000} placeholder="Что нужно сделать?" aria-label="Новое дело"/><button className="add" type="submit" disabled={pending || !text.trim()} aria-label="Добавить дело">{pending ? <LoaderCircle className="spin" size={18}/> : <Plus size={22}/>}</button></form><a className="voice-link" href="https://t.me/taskovio_bot"><Mic size={13}/>Голосом можно в чате с ботом</a></footer>
    {selected ? <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) setSelected(null); }}><section className="sheet" role="dialog" aria-modal="true" aria-label="Дело"><div className="sheet-head"><h2>Дело</h2><button className="icon" onClick={() => setSelected(null)} aria-label="Закрыть"><X size={21}/></button></div>{error ? <p role="alert" className="error">{error}</p> : null}<TaskForm key={selected.id} task={selected} latest={tasks.data?.find(t => t.id === selected.id)} pending={pending} save={(data) => void act(() => api(`/personal/tasks/${selected.id}`, 'PATCH', data), () => setSelected(null))} remove={() => { if (window.confirm('Удалить дело и остановить его напоминания?')) void act(() => api(`/personal/tasks/${selected.id}`, 'PATCH', { version: tasks.data?.find(t => t.id === selected.id)?.version || selected.version, deleted: true }), () => setSelected(null)); }}/></section></div> : null}
  </div>;
}

function TaskForm({ task, latest, pending, save, remove }: { task: Todo; latest?: Todo; pending: boolean; save: (value: Record<string, unknown>) => void; remove: () => void }) {
  const [title, setTitle] = useState(task.title);
  const [note, setNote] = useState(task.note);
  const [due, setDue] = useState(task.due_at ? dayKey(task.due_at) : '');
  const [bucket, setBucket] = useState(task.bucket);
  const [enabled, setEnabled] = useState(Boolean(task.reminder?.enabled));
  const [when, setWhen] = useState(task.reminder?.enabled ? localInput(task.reminder.next_at) : '');
  const [interval, setInterval] = useState(task.reminder?.interval_seconds || 0);
  const [more, setMore] = useState(Boolean(task.note));
  const submit = () => {
    const sameDay = due === (task.due_at ? dayKey(task.due_at) : '');
    save({ version: task.version, title, note, due_at: due ? sameDay ? task.due_at : due + 'T23:59:00+03:00' : null, due_has_time: sameDay ? task.due_has_time : false, bucket,
      reminder_enabled: enabled && !task.done_at, ...(enabled ? { reminder_at: when + ':00+03:00', reminder_interval: interval } : {}) });
  };
  return <form onSubmit={e => { e.preventDefault(); submit(); }}>
    {latest && latest.version !== task.version ? <p className="error">Дело изменено в другом месте. Закройте карточку и откройте заново.</p> : null}
    <label>Что сделать<input required maxLength={500} value={title} onChange={e => setTitle(e.target.value)}/></label>
    <label>Срок<input type="date" value={due} onChange={e => setDue(e.target.value)}/></label>
    <label>Куда положить<select value={bucket} onChange={e => setBucket(e.target.value)}><option value="inbox">Входящие</option><option value="today">Сегодня</option><option value="later">Позже</option></select></label>
    {!task.done_at ? <div className="reminder-box"><label className="check-label"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)}/><Bell size={16}/>Напомнить мне</label>{enabled ? <><div className="quick-times"><button type="button" onClick={() => setWhen(localInput(new Date(Date.now()+3600000).toISOString()))}>Через час</button>{due ? <button type="button" onClick={() => { const date = new Date(due + 'T09:00:00+03:00'); date.setTime(date.getTime()-86400000); setWhen(localInput(date.toISOString())); }}>За день, 09:00</button> : null}</div><label>Когда, по Москве<input type="datetime-local" required value={when} onChange={e => setWhen(e.target.value)}/></label><label>Как часто<select value={interval} onChange={e => setInterval(Number(e.target.value))}>{Object.entries(frequencies).map(([seconds,label]) => <option key={seconds} value={seconds}>{label}</option>)}</select></label><small>Повторять до выполнения дела.</small></> : null}</div> : null}
    {more ? <label>Заметка<textarea value={note} maxLength={10000} onChange={e => setNote(e.target.value)}/></label> : <button type="button" className="quiet add-note" onClick={() => setMore(true)}>Добавить заметку</button>}
    <button className="primary save" disabled={pending || Boolean(latest && latest.version !== task.version)}>{pending ? 'Сохраняю...' : 'Сохранить'}</button><button type="button" className="delete" disabled={pending} onClick={remove}>Удалить дело</button>
  </form>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={queryClient}><App/></QueryClientProvider></React.StrictMode>);
