import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider, useQuery } from '@tanstack/react-query';
import { DndContext, PointerSensor, TouchSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core';
import { ArrowLeft, ArrowRight, Check, ChevronRight, Circle, Clock, FolderKanban, GitBranch, LayoutDashboard, List, LoaderCircle, Plus, Search, Settings as SettingsIcon, Trash2, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { api, download, queryClient } from './api';
import type { Activity, Dashboard, Project, Repository, Sprint, Status, Task, TimeEntry, User } from './types';
import './style.css';

const fmt = (seconds: number) => `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`;
const dateLabel = (value: string | null) => value ? new Date(value + 'T12:00:00').toLocaleDateString('ru', { day: 'numeric', month: 'short' }) : 'Без срока';
const timeLabel = (value: string) => new Date(value).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const priorityLabels: Record<string, string> = { Low: 'Низкий', Medium: 'Средний', High: 'Высокий', Critical: 'Критический' };
const activityLabels: Record<string, string> = { created: 'Создана задача', updated: 'Задача изменена', comment: 'Комментарий', checklist_added: 'Добавлен пункт', checklist_updated: 'Обновлён чек-лист', checklist_deleted: 'Удалён пункт', attachment_added: 'Добавлен файл', timer_start: 'Таймер запущен', timer_pause: 'Таймер на паузе', timer_stop: 'Таймер остановлен', time_added: 'Добавлено время', github: 'Событие GitHub', github_linked: 'Добавлена связь GitHub', project_created: 'Создан проект', member_joined: 'Участник присоединился', member_removed: 'Участник удалён', sprint_created: 'Создан спринт', sprint_active: 'Начат спринт', sprint_completed: 'Завершён спринт', statuses_reordered: 'Изменён порядок статусов', status_created: 'Создан статус', status_updated: 'Статус изменён', project_updated: 'Проект изменён', owner_changed: 'Владелец изменён', repository_connected: 'Подключён репозиторий', repository_disconnected: 'Репозиторий отключён' };
type Action = <T>(fn: () => Promise<T>, after?: (result: T) => void) => Promise<void>;

function App() {
  const [projectId, setProjectId] = useState<number | null>(null);
  const [taskId, setTaskId] = useState<number | null>(() => Number(new URLSearchParams(location.search).get('task')) || null);
  const [screen, setScreen] = useState('dashboard');
  const [modal, setModal] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const me = useQuery({ queryKey: ['me'], queryFn: () => api<User>('/me'), enabled: Boolean(window.Telegram?.WebApp.initData) });
  const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api<Project>(`/projects/${projectId}`), enabled: Boolean(projectId) });
  const back = () => { if (taskId) setTaskId(null); else if (modal) setModal(null); else { setProjectId(null); setScreen('dashboard'); } };
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    tg?.ready(); tg?.expand();
    const theme = () => document.documentElement.dataset.theme = tg?.colorScheme || 'light';
    theme(); tg?.onEvent('themeChanged', theme);
    return () => tg?.offEvent('themeChanged', theme);
  }, []);
  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (projectId || taskId || modal || screen !== 'dashboard') tg?.BackButton.show(); else tg?.BackButton.hide();
    tg?.BackButton.onClick(back);
    return () => tg?.BackButton.offClick(back);
  }, [projectId, taskId, modal, screen]);
  const act: Action = async (fn, after) => {
    if (pending) return;
    setPending(true); setError('');
    try { const result = await fn(); await queryClient.invalidateQueries(); after?.(result); window.Telegram?.WebApp.HapticFeedback?.notificationOccurred('success'); }
    catch (e) { setError((e as Error).message); await queryClient.invalidateQueries(); }
    finally { setPending(false); }
  };
  const openProject = (id: number) => { setProjectId(id); setScreen('project'); };
  if (!window.Telegram?.WebApp.initData) return <main className="welcome"><div className="brand-icon"><FolderKanban size={38}/></div><h1>TaskMaster</h1><p>Ваши проекты, задачи и время.<br/>Откройте приложение кнопкой в Telegram-боте.</p><small>Данные проектов доступны после входа через Telegram.</small></main>;
  return <div className="app">
    <header><div className="brand"><span className="brand-icon"><FolderKanban size={20}/></span><strong>TaskMaster</strong></div><span className="avatar">{me.data?.name.slice(0, 1) || 'T'}</span></header>
    {error || me.error ? <div className="error" role="alert">{error || (me.error as Error).message}<button onClick={() => setError('')} aria-label="Закрыть ошибку"><X size={16}/></button></div> : null}
    {notice ? <div className="notice">{notice}<button onClick={() => setNotice('')}><X size={16}/></button></div> : null}
    <main>
      {projectId ? <div className="project-heading"><button className="icon-button" onClick={back} aria-label="Назад"><ArrowLeft size={20}/></button><div><span className="eyebrow">{project.data?.key || 'Проект'}</span><h1>{project.data?.name || 'Загрузка...'}</h1></div></div> : null}
      {screen === 'dashboard' ? <DashboardView openProject={openProject} openTask={setTaskId} newProject={() => setModal('project')} newTask={() => setModal('task')}/> : null}
      {screen === 'project' && project.data ? <ProjectView project={project.data} act={act} pending={pending} openTask={setTaskId} newTask={() => setModal('task')} user={me.data} notice={setNotice} /> : null}
      {screen === 'time' ? <TimeView act={act} pending={pending} openTask={setTaskId}/> : null}
      {project.isError ? <Empty text={(project.error as Error).message}/> : null}
    </main>
    <nav className="bottom-nav"><button className={screen === 'dashboard' ? 'active' : ''} onClick={() => { setProjectId(null); setScreen('dashboard'); }}><LayoutDashboard size={20}/>Обзор</button><button onClick={() => setModal('task')}><Plus size={22}/>Задача</button><button className={screen === 'time' ? 'active' : ''} onClick={() => { setProjectId(null); setScreen('time'); }}><Clock size={20}/>Время</button></nav>
    {pending ? <div className="saving"><LoaderCircle size={16} className="spin"/>Сохранение</div> : null}
    {modal === 'project' ? <Modal title="Новый проект" close={() => setModal(null)}><ProjectForm act={act} pending={pending} done={id => { setModal(null); openProject(id); }}/></Modal> : null}
    {modal === 'task' ? <Modal title="Новая задача" close={() => setModal(null)}><NewTask projectId={projectId} act={act} pending={pending} done={id => { setModal(null); setTaskId(id); }}/></Modal> : null}
    {taskId ? <Modal title="Задача" close={() => setTaskId(null)} wide><TaskDetails id={taskId} act={act} pending={pending} close={() => setTaskId(null)}/></Modal> : null}
  </div>;
}

function Empty({ text }: { text: string }) { return <div className="empty"><Circle size={28}/><p>{text}</p></div>; }
function Loading() { return <div className="loading"><LoaderCircle className="spin"/>Загрузка...</div>; }
function Modal({ title, close, children, wide = false }: { title: string; close: () => void; children: React.ReactNode; wide?: boolean }) {
  return <div className="overlay" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><section className={'modal ' + (wide ? 'wide' : '')} role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" onClick={close} aria-label="Закрыть"><X/></button></div>{children}</section></div>;
}
function TaskCard({ task, open, compact = false }: { task: Task; open: (id: number) => void; compact?: boolean }) {
  const draggable = useDraggable({ id: task.id, disabled: compact });
  return <article ref={draggable.setNodeRef} style={draggable.transform ? { transform: `translate3d(${draggable.transform.x}px, ${draggable.transform.y}px, 0)`, position: 'relative', zIndex: 3 } : undefined} className={'task-card ' + (draggable.isDragging ? 'dragging' : '')}>
    <div className="card-top"><span className="task-key" {...(!compact ? { ...draggable.listeners, ...draggable.attributes } : {})}>{task.key}{!compact ? <span className="drag-hint"> ⋮⋮</span> : null}</span><span className={'priority ' + task.priority.toLowerCase()}>{priorityLabels[task.priority]}</span></div>
    <button className="task-title" onClick={() => open(task.id)}>{task.title}</button>
    {task.deadline || task.assignee_id ? <footer>{task.deadline ? <span><Clock size={12}/>{dateLabel(task.deadline)}</span> : <span/>}{task.assignee_id ? <span className="mini-avatar">Назначена</span> : null}</footer> : null}
  </article>;
}
function DashboardView({ openProject, openTask, newProject, newTask }: { openProject: (id: number) => void; openTask: (id: number) => void; newProject: () => void; newTask: () => void }) {
  const query = useQuery({ queryKey: ['dashboard'], queryFn: () => api<Dashboard>('/dashboard') });
  if (query.isPending) return <Loading/>;
  if (query.error) return <Empty text={(query.error as Error).message}/>;
  const data = query.data;
  return <><div className="page-title"><div><span className="eyebrow">ВАШЕ РАБОЧЕЕ ПРОСТРАНСТВО</span><h1>Обзор</h1><p>Всё важное на сегодня</p></div><button className="primary round" onClick={newTask} aria-label="Создать задачу"><Plus/></button></div>
    <div className="stats"><div><strong>{data.active_tasks}</strong><span>Активные задачи</span></div><div><strong>{data.today.length}</strong><span>На сегодня</span></div><div className={data.overdue.length ? 'danger' : ''}><strong>{data.overdue.length}</strong><span>Просрочено</span></div></div>
    <div className="section-heading"><h2>Проекты <span>{data.projects.length}</span></h2><button onClick={newProject}><Plus size={16}/>Создать</button></div>
    <div className="projects-grid">{data.projects.map(p => <button className="project-tile" key={p.id} onClick={() => openProject(p.id)}><span className="project-badge">{p.key.slice(0, 2)}</span><div><strong>{p.name}</strong><small>{p.key} · {p.active_tasks} активных задач</small></div><ChevronRight size={18}/></button>)}</div>
    {!data.projects.length ? <Empty text="Создайте первый проект и добавьте задачу"/> : null}
    {[['На сегодня', data.today], ['Просрочено', data.overdue], ['Ближайшие дедлайны', data.upcoming]].map(([label, tasks]) => (tasks as Task[]).length ? <section key={label as string}><h2>{label as string}</h2><div className="task-list">{(tasks as Task[]).map(t => <TaskCard key={t.id} task={t} open={openTask} compact/>)}</div></section> : null)}
  </>;
}
function ProjectForm({ act, pending, done }: { act: Action; pending: boolean; done: (id: number) => void }) {
  const [name, setName] = useState(''); const [key, setKey] = useState(''); const [description, setDescription] = useState('');
  return <form onSubmit={e => { e.preventDefault(); void act(() => api<Project>('/projects', 'POST', { name, key, description }), p => done(p.id)); }}><label>Название<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="Например, Altair"/></label><label>Ключ проекта<input required pattern="[A-Z][A-Z0-9]{1,11}" maxLength={12} value={key} onChange={e => setKey(e.target.value.toUpperCase())} placeholder="ALT"/><small>Задачи получат ключи ALT-1, ALT-2...</small></label><label>Описание<textarea value={description} onChange={e => setDescription(e.target.value)} maxLength={10000}/></label><button className="primary" disabled={pending}>Создать приватный проект</button></form>;
}
function NewTask({ projectId, act, pending, done }: { projectId: number | null; act: Action; pending: boolean; done: (id: number) => void }) {
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api<Project[]>('/projects') });
  const [selected, setSelected] = useState(projectId || 0); const [title, setTitle] = useState(''); const [deadline, setDeadline] = useState('');
  useEffect(() => { if (!selected && projects.data?.length) setSelected(projects.data[0].id); }, [projects.data, selected]);
  return <form onSubmit={e => { e.preventDefault(); void act(() => api<Task>(`/projects/${selected}/tasks`, 'POST', { title, deadline: deadline || null }), t => done(t.id)); }}><label>Проект<select required value={selected} onChange={e => setSelected(Number(e.target.value))}><option value={0} disabled>Выберите проект</option>{projects.data?.map(p => <option key={p.id} value={p.id}>{p.key} · {p.name}</option>)}</select></label><label>Что нужно сделать?<input autoFocus required maxLength={300} value={title} onChange={e => setTitle(e.target.value)} placeholder="Короткий заголовок задачи"/></label><label>Дедлайн, необязательно<input type="date" value={deadline} onChange={e => setDeadline(e.target.value)}/></label><button disabled={pending || !selected} className="primary">Создать задачу</button>{!projects.data?.length ? <small>Сначала создайте проект на экране «Обзор».</small> : null}</form>;
}
function Column({ status, children }: { status: Status; children: React.ReactNode }) { const drop = useDroppable({ id: 'status:' + status.id }); return <div ref={drop.setNodeRef} className={'kanban-column ' + (drop.isOver ? 'drop-over' : '')}><h3><span className={'status-dot ' + (status.is_done ? 'done' : '')}/>{status.name}</h3>{children}</div>; }

function ProjectView({ project, act, pending, openTask, newTask, user, notice }: { project: Project; act: Action; pending: boolean; openTask: (id: number) => void; newTask: () => void; user?: User; notice: (value: string) => void }) {
  const [tab, setTab] = useState('board'); const [q, setQ] = useState(''); const [mine, setMine] = useState(false); const [deleted, setDeleted] = useState(false); const [priority, setPriority] = useState(''); const [statusFilter, setStatusFilter] = useState(0);
  const tasks = useQuery({ queryKey: ['tasks', project.id, q, mine, deleted], queryFn: () => api<Task[]>(`/projects/${project.id}/tasks?q=${encodeURIComponent(q)}&mine=${mine}&deleted=${deleted}`) });
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(TouchSensor, { activationConstraint: { delay: 180, tolerance: 6 } }));
  const visible = tasks.data?.filter(t => (!priority || t.priority === priority) && (!statusFilter || t.status_id === statusFilter)) || [];
  const drop = (event: DragEndEvent) => { const task = tasks.data?.find(t => t.id === event.active.id); const id = Number(String(event.over?.id).split(':')[1]); if (task && id && task.status_id !== id) void act(() => api(`/tasks/${task.id}`, 'PATCH', { version: task.version, status_id: id })); };
  return <><div className="tabs">{[['board', 'Kanban'], ['list', 'Список'], ['sprints', 'Спринты'], ['activity', 'История'], ['settings', 'Настройки']].map(([key, label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{label}</button>)}</div>
    {tab === 'board' || tab === 'list' ? <><div className="toolbar"><div className="search"><Search size={16}/><input placeholder="Поиск задач" value={q} onChange={e => setQ(e.target.value)}/></div><button className="primary" onClick={newTask}><Plus size={16}/>Задача</button></div><div className="filters"><label className="check-label"><input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)}/>Назначены мне</label><label className="check-label"><input type="checkbox" checked={deleted} onChange={e => setDeleted(e.target.checked)}/>Удалённые</label><select aria-label="Приоритет" value={priority} onChange={e => setPriority(e.target.value)}><option value="">Все приоритеты</option>{Object.entries(priorityLabels).map(([p, label]) => <option key={p} value={p}>{label}</option>)}</select><select aria-label="Статус" value={statusFilter} onChange={e => setStatusFilter(Number(e.target.value))}><option value={0}>Все статусы</option>{project.statuses.map(s => <option value={s.id} key={s.id}>{s.name}</option>)}</select></div>
      {tasks.isPending ? <Loading/> : tasks.error ? <Empty text={(tasks.error as Error).message}/> : tab === 'board' ? <DndContext sensors={sensors} onDragEnd={drop}><div className="kanban">{project.statuses.map(s => <Column key={s.id} status={s}>{visible.filter(t => t.status_id === s.id).map(t => <TaskCard key={t.id} task={t} open={openTask}/>)}<small>{visible.filter(t => t.status_id === s.id).length} задач</small></Column>)}</div><small className="hint">Перетаскивайте карточки за ключ задачи. На телефоне удерживайте его.</small></DndContext> : <div className="task-list">{visible.length ? visible.map(t => <TaskCard key={t.id} task={t} open={openTask} compact/>) : <Empty text="Нет задач с выбранными фильтрами"/>}</div>}</> : null}
    {tab === 'sprints' ? <SprintView project={project} tasks={tasks.data || []} act={act} pending={pending} openTask={openTask}/> : null}
    {tab === 'activity' ? <ActivityView projectId={project.id} openTask={openTask}/> : null}
    {tab === 'settings' ? <ProjectSettings project={project} act={act} pending={pending} user={user} notice={notice}/> : null}
  </>;
}

function TaskDetails({ id, act, pending, close }: { id: number; act: Action; pending: boolean; close: () => void }) {
  const taskQuery = useQuery({ queryKey: ['task', id], queryFn: () => api<Task>(`/tasks/${id}`) });
  const task = taskQuery.data;
  const project = useQuery({ queryKey: ['project', task?.project_id], queryFn: () => api<Project>(`/projects/${task?.project_id}`), enabled: Boolean(task) });
  const sprints = useQuery({ queryKey: ['sprints', task?.project_id], queryFn: () => api<Sprint[]>(`/projects/${task?.project_id}/sprints`), enabled: Boolean(task) });
  const timer = useQuery({ queryKey: ['timer'], queryFn: () => api<TimeEntry | null>('/timer') });
  const [title, setTitle] = useState(''); const [description, setDescription] = useState(''); const [comment, setComment] = useState(''); const [item, setItem] = useState(''); const [minutes, setMinutes] = useState(''); const [note, setNote] = useState(''); const [url, setUrl] = useState(''); const [editing, setEditing] = useState(false); const [editVersion, setEditVersion] = useState(0);
  const patch = (data: Record<string, unknown>) => task && void act(() => api(`/tasks/${id}`, 'PATCH', { version: task.version, ...data }));
  if (taskQuery.isPending) return <Loading/>;
  if (taskQuery.error || !task) return <Empty text={(taskQuery.error as Error)?.message || 'Задача не найдена'}/>;
  const beginEdit = () => { setTitle(task.title); setDescription(task.description); setEditVersion(task.version); setEditing(true); };
  return <div className="task-details"><span className="eyebrow">{task.key}</span>{editing ? <form onSubmit={e => { e.preventDefault(); void act(() => api(`/tasks/${id}`, 'PATCH', { version: editVersion, title, description }), () => setEditing(false)); }}><label>Заголовок<input required maxLength={300} value={title} onChange={e => setTitle(e.target.value)}/></label><label>Описание (Markdown)<textarea rows={6} value={description} onChange={e => setDescription(e.target.value)} maxLength={50000}/></label><div className="actions"><button className="primary" disabled={pending}>Сохранить</button><button type="button" onClick={() => setEditing(false)}>Отмена</button></div></form> : <><h1>{task.title}</h1><div className="markdown"><ReactMarkdown>{task.description || 'Описание пока не добавлено.'}</ReactMarkdown></div><button className="text-button" onClick={beginEdit}>Редактировать название и описание</button></>}
    {task.deleted_at ? <div className="notice">Задача удалена<button disabled={pending} onClick={() => patch({ deleted: false })}>Восстановить</button></div> : null}
    <div className="field-grid"><label>Статус<select disabled={pending} value={task.status_id} onChange={e => patch({ status_id: Number(e.target.value) })}>{project.data?.statuses.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>Приоритет<select disabled={pending} value={task.priority} onChange={e => patch({ priority: e.target.value })}>{Object.entries(priorityLabels).map(([p, label]) => <option key={p} value={p}>{label}</option>)}</select></label><label>Исполнитель<select disabled={pending} value={task.assignee_id || 0} onChange={e => patch({ assignee_id: Number(e.target.value) || null })}><option value={0}>Не назначен</option>{project.data?.members.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label><label>Дедлайн<input disabled={pending} type="date" value={task.deadline || ''} onChange={e => patch({ deadline: e.target.value || null })}/></label><label>Спринт<select disabled={pending} value={task.sprint_id || 0} onChange={e => patch({ sprint_id: Number(e.target.value) || null })}><option value={0}>Без спринта</option>{sprints.data?.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label></div>
    <section><h2><Clock size={17}/>Учёт времени <span>{fmt(task.total_seconds)}</span></h2>{timer.data?.task_id === id ? <TimerBar entry={timer.data} act={act} pending={pending}/> : <button disabled={pending || Boolean(task.deleted_at)} onClick={() => void act(() => api(`/timer/${id}/start`, 'POST'))}>Запустить таймер</button>}<form className="inline-form" onSubmit={e => { e.preventDefault(); void act(() => api(`/tasks/${id}/time`, 'POST', { seconds: Number(minutes) * 60, note }), () => { setMinutes(''); setNote(''); }); }}><input aria-label="Минуты" type="number" required min={1} max={10080} value={minutes} onChange={e => setMinutes(e.target.value)} placeholder="Минуты"/><input aria-label="Примечание" value={note} onChange={e => setNote(e.target.value)} placeholder="Примечание" maxLength={500}/><button disabled={pending}>Добавить</button></form></section>
    <section><h2>Чек-лист <span>{task.checklist.filter(c => c.done).length}/{task.checklist.length}</span></h2>{task.checklist.map(c => <div className="check-item" key={c.id}><label><input disabled={pending} type="checkbox" checked={c.done} onChange={e => void act(() => api(`/tasks/${id}/checklist/${c.id}`, 'PATCH', { done: e.target.checked }))}/><span className={c.done ? 'checked' : ''}>{c.text}</span></label><button className="icon-button" disabled={pending} onClick={() => void act(() => api(`/tasks/${id}/checklist/${c.id}`, 'DELETE'))} aria-label="Удалить пункт"><X size={15}/></button></div>)}<form className="inline-form" onSubmit={e => { e.preventDefault(); void act(() => api(`/tasks/${id}/checklist`, 'POST', { text: item }), () => setItem('')); }}><input required maxLength={500} placeholder="Новый пункт" value={item} onChange={e => setItem(e.target.value)} aria-label="Новый пункт"/><button disabled={pending}><Plus size={16}/></button></form></section>
    <section><h2>Вложения</h2><small>PNG, JPG, WEBP, PDF, TXT. До 10 МБ.</small>{task.attachments.map(a => <button className="attachment" key={a.id} onClick={() => void act(() => download(a.id, a.name))}>{a.name}<small>{(a.size / 1024).toFixed(0)} КБ</small></button>)}<label className="file-label">Добавить файл<input disabled={pending} type="file" accept=".png,.jpg,.jpeg,.webp,.pdf,.txt" onChange={e => { const file = e.target.files?.[0]; if (file) { const data = new FormData(); data.append('file', file); void act(() => api(`/tasks/${id}/attachments`, 'POST', data)); } e.target.value = ''; }}/></label></section>
    <section><h2>Комментарии</h2>{task.comments.map(c => <div className="comment" key={c.id}><small>{project.data?.members.find(m => m.id === c.author_id)?.name || 'Участник'} · {timeLabel(c.created_at)}</small><ReactMarkdown>{c.text}</ReactMarkdown></div>)}<form onSubmit={e => { e.preventDefault(); void act(() => api(`/tasks/${id}/comments`, 'POST', { text: comment }), () => setComment('')); }}><textarea required maxLength={10000} placeholder="Написать комментарий" value={comment} onChange={e => setComment(e.target.value)} aria-label="Комментарий"/><button className="primary" disabled={pending}>Отправить</button></form></section>
    <section><h2><GitBranch size={18}/>GitHub</h2>{task.github.map(g => <a key={g.id} className="github-link" href={g.url} target="_blank" rel="noreferrer">{g.kind === 'pr' ? 'PR' : 'Коммит'} · {g.title.split('\n')[0]} {g.state ? `(${g.state})` : ''}<ArrowRight size={15}/></a>)}<form className="inline-form" onSubmit={e => { e.preventDefault(); void act(() => api(`/tasks/${id}/github`, 'POST', { url }), () => setUrl('')); }}><input type="url" required placeholder="Ссылка на PR или коммит" value={url} onChange={e => setUrl(e.target.value)} aria-label="GitHub ссылка"/><button disabled={pending}>Связать</button></form></section>
    <section><h2>История</h2><ActivityList events={task.activity} project={project.data}/></section>
    {!task.deleted_at ? <button className="danger-button" disabled={pending} onClick={() => { if (window.confirm('Удалить задачу? Её можно восстановить в списке удалённых.')) void act(() => api(`/tasks/${id}`, 'PATCH', { version: task.version, deleted: true }), close); }}><Trash2 size={16}/>Удалить задачу</button> : null}
  </div>;
}

function TimerBar({ entry, act, pending }: { entry: TimeEntry; act: Action; pending: boolean }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  const seconds = entry.seconds + (entry.running_since ? Math.max(0, (now - new Date(entry.running_since).getTime()) / 1000) : 0);
  return <div className="timer-bar"><div><span className="eyebrow">{entry.running_since ? 'ТАЙМЕР РАБОТАЕТ' : 'НА ПАУЗЕ'}</span><strong>{fmt(seconds)}</strong></div><button disabled={pending} onClick={() => void act(() => api(entry.running_since ? '/timer/pause' : `/timer/${entry.task_id}/start`, 'POST'))}>{entry.running_since ? 'Пауза' : 'Продолжить'}</button><button disabled={pending} onClick={() => void act(() => api('/timer/stop', 'POST'))}>Стоп</button></div>;
}
function TimeView({ act, pending, openTask }: { act: Action; pending: boolean; openTask: (id: number) => void }) {
  const timer = useQuery({ queryKey: ['timer'], queryFn: () => api<TimeEntry | null>('/timer') });
  const entries = useQuery({ queryKey: ['time'], queryFn: () => api<TimeEntry[]>('/time') });
  const totals = new Map<string, number>(); entries.data?.forEach(e => totals.set(e.project_name || '', (totals.get(e.project_name || '') || 0) + e.seconds));
  return <><div className="page-title"><div><span className="eyebrow">ФОКУС И ПРОГРЕСС</span><h1>Учёт времени</h1><p>Время сохраняется на сервере</p></div></div>{timer.data ? <TimerBar entry={timer.data} act={act} pending={pending}/> : <Empty text="Нет активного таймера. Запустите его в задаче."/>}<h2>По проектам</h2><div className="stats">{[...totals].map(([name, seconds]) => <div key={name}><strong>{fmt(seconds)}</strong><span>{name}</span></div>)}</div><h2>Записи времени</h2>{entries.isPending ? <Loading/> : entries.error ? <Empty text={(entries.error as Error).message}/> : entries.data?.map(e => <button key={e.id} className="time-row" onClick={() => openTask(e.task_id)}><div><strong>{e.task_key} · {e.task_title}</strong><small>{timeLabel(e.started_at)}{e.note ? ' · ' + e.note : ''}</small></div><span>{fmt(e.seconds)}</span></button>)}</>;
}
function SprintView({ project, tasks, act, pending, openTask }: { project: Project; tasks: Task[]; act: Action; pending: boolean; openTask: (id: number) => void }) {
  const sprints = useQuery({ queryKey: ['sprints', project.id], queryFn: () => api<Sprint[]>(`/projects/${project.id}/sprints`) });
  const [creating, setCreating] = useState(false); const [name, setName] = useState(''); const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  return <><div className="section-heading"><h2>Спринты</h2><button onClick={() => setCreating(!creating)}><Plus size={16}/>Новый</button></div>{creating ? <form className="panel" onSubmit={e => { e.preventDefault(); void act(() => api(`/projects/${project.id}/sprints`, 'POST', { name, start_date: start, end_date: end }), () => { setCreating(false); setName(''); }); }}><label>Название<input required maxLength={120} value={name} onChange={e => setName(e.target.value)}/></label><div className="field-grid"><label>Начало<input required type="date" value={start} onChange={e => setStart(e.target.value)}/></label><label>Окончание<input required type="date" min={start} value={end} onChange={e => setEnd(e.target.value)}/></label></div><button className="primary" disabled={pending}>Создать</button></form> : null}
    {sprints.error ? <Empty text={(sprints.error as Error).message}/> : sprints.data?.map(s => <section key={s.id} className="panel"><div className="section-heading"><div><h3>{s.name}</h3><small>{dateLabel(s.start_date)} - {dateLabel(s.end_date)} · {{ planned: 'Запланирован', active: 'Активный', completed: 'Завершён' }[s.state]}</small></div>{s.state !== 'completed' ? <button disabled={pending} onClick={() => void act(() => api(`/projects/${project.id}/sprints/${s.id}`, 'PATCH', { state: s.state === 'planned' ? 'active' : 'completed' }))}>{s.state === 'planned' ? 'Начать' : 'Завершить'}</button> : <span>{s.result?.done}/{s.result?.total} выполнено</span>}</div><div className="task-list">{tasks.filter(t => t.sprint_id === s.id).map(t => <TaskCard key={t.id} task={t} open={openTask} compact/>)}</div></section>)}
    <h2>Backlog · без спринта</h2><small>Назначить спринт можно в карточке задачи.</small><div className="task-list">{tasks.filter(t => !t.sprint_id).map(t => <TaskCard key={t.id} task={t} open={openTask} compact/>)}</div></>;
}
function describeActivity(event: Activity, project?: Project): string {
  const data = event.data;
  if (event.kind === 'updated') {
    const names: Record<string, string> = { title: 'Заголовок', description: 'Описание', status_id: 'Статус', priority: 'Приоритет', assignee_id: 'Исполнитель', deadline: 'Дедлайн', sprint_id: 'Спринт', deleted_at: 'Удаление' };
    const format = (key: string, value: unknown): string => {
      if (key === 'deleted_at') return value ? 'Удалена' : 'Восстановлена';
      if (value === null) return 'Не задано';
      if (key === 'status_id') return project?.statuses.find(s => s.id === value)?.name || 'Статус изменён';
      if (key === 'assignee_id') return project?.members.find(m => m.id === value)?.name || 'Участник';
      if (key === 'sprint_id') return 'Назначен спринт';
      if (key === 'priority') return priorityLabels[String(value)] || String(value);
      return String(value).slice(0, 120);
    };
    return Object.entries(data).map(([key, value]) => { const change = value as { before: unknown; after: unknown }; return `${names[key] || 'Поле'}: ${format(key, change.before)} → ${format(key, change.after)}`; }).join('\n');
  }
  if (typeof data.seconds === 'number') return fmt(data.seconds);
  if (typeof data.done === 'boolean') return data.done ? 'Пункт выполнен' : 'Пункт снова открыт';
  if (typeof data.text === 'string') return data.text.slice(0, 300);
  if (typeof data.title === 'string') return data.title;
  if (typeof data.name === 'string') return data.name;
  if (typeof data.full_name === 'string') return data.full_name;
  if (typeof data.url === 'string') return data.url;
  if (typeof data.owner_id === 'number') return project?.members.find(m => m.id === data.owner_id)?.name || 'Новый владелец';
  return '';
}
function ActivityList({ events, openTask, project }: { events: Activity[]; openTask?: (id: number) => void; project?: Project }) { return <div className="activity-list">{events.map(e => <div key={e.id}><span className="activity-dot"/><div><strong>{activityLabels[e.kind] || 'Изменение проекта'}</strong><small>{timeLabel(e.created_at)} · {e.actor_id ? project?.members.find(m => m.id === e.actor_id)?.name || 'Участник' : 'Система'}</small><pre>{describeActivity(e, project)}</pre>{e.task_id && openTask ? <button className="text-button" onClick={() => openTask(e.task_id!)}>Открыть задачу</button> : null}</div></div>)}{!events.length ? <Empty text="История пока пуста"/> : null}</div>; }
function ActivityView({ projectId, openTask }: { projectId: number; openTask: (id: number) => void }) { const query = useQuery({ queryKey: ['activity', projectId], queryFn: () => api<Activity[]>(`/projects/${projectId}/activity`) }); const project = useQuery({ queryKey: ['project', projectId], queryFn: () => api<Project>(`/projects/${projectId}`) }); return query.isPending ? <Loading/> : query.error ? <Empty text={(query.error as Error).message}/> : <ActivityList events={query.data} openTask={openTask} project={project.data}/>; }

function ProjectSettings({ project, act, pending, user, notice }: { project: Project; act: Action; pending: boolean; user?: User; notice: (value: string) => void }) {
  const [name, setName] = useState(project.name); const [description, setDescription] = useState(project.description); const [statusName, setStatusName] = useState(''); const [done, setDone] = useState(false); const [target, setTarget] = useState(''); const [inviteUrl, setInviteUrl] = useState(''); const [repoName, setRepoName] = useState(''); const [token, setToken] = useState(''); const [newRepo, setNewRepo] = useState<Repository | null>(null); const [confirmKey, setConfirmKey] = useState(''); const [newOwner, setNewOwner] = useState(0);
  const preferences = useQuery({ queryKey: ['preferences', project.id], queryFn: () => api<Record<string, boolean>>(`/projects/${project.id}/notifications`) });
  const repositories = useQuery({ queryKey: ['repositories', project.id], queryFn: () => api<Repository[]>(`/projects/${project.id}/repositories`) });
  const eventNames: Record<string, string> = { assigned: 'Назначение задачи', status: 'Изменение статуса', comment: 'Комментарии', deadline: 'Приближение дедлайна', overdue: 'Просроченные задачи', github: 'GitHub', sprint: 'Спринты' };
  const reorder = (index: number, offset: number) => { const ids = project.statuses.map(s => s.id); const to = index + offset; [ids[index], ids[to]] = [ids[to], ids[index]]; void act(() => api(`/projects/${project.id}/status-order`, 'PUT', { ids })); };
  return <><section className="panel"><h2>Проект</h2><form onSubmit={e => { e.preventDefault(); void act(() => api(`/projects/${project.id}`, 'PATCH', { name, description })); }}><label>Название<input required value={name} maxLength={120} onChange={e => setName(e.target.value)}/></label><label>Описание<textarea value={description} maxLength={10000} onChange={e => setDescription(e.target.value)}/></label><button disabled={pending}>Сохранить</button></form></section>
    <section className="panel"><h2>Статусы</h2>{project.statuses.map((s, index) => <StatusEditor key={s.id} status={s} pending={pending} save={(name, is_done) => void act(() => api(`/projects/${project.id}/statuses/${s.id}`, 'PATCH', { name, is_done }))} up={index ? () => reorder(index, -1) : undefined} down={index < project.statuses.length - 1 ? () => reorder(index, 1) : undefined}/>)}<form onSubmit={e => { e.preventDefault(); void act(() => api(`/projects/${project.id}/statuses`, 'POST', { name: statusName, is_done: done }), () => { setStatusName(''); setDone(false); }); }}><label>Новый статус<input required maxLength={80} value={statusName} onChange={e => setStatusName(e.target.value)}/></label><label className="check-label"><input type="checkbox" checked={done} onChange={e => setDone(e.target.checked)}/>Завершающий статус</label><button disabled={pending}>Добавить статус</button></form></section>
    <section className="panel"><h2>Участники</h2>{project.members.map(m => <div className="member-row" key={m.id}><div><strong>{m.name}</strong><small>{m.username ? '@' + m.username : m.telegram_id}{m.id === project.owner_id ? ' · Владелец' : ''}</small></div>{m.id !== project.owner_id ? <button disabled={pending} onClick={() => { if (window.confirm('Убрать участника из проекта?')) void act(() => api(`/projects/${project.id}/members/${m.id}`, 'DELETE')); }}>Убрать</button> : null}</div>)}<form onSubmit={e => { e.preventDefault(); void act(() => api<{ url: string }>(`/projects/${project.id}/invites`, 'POST', { target: target || null }), value => setInviteUrl(value.url)); }}><label>Telegram ID или username, необязательно<input value={target} onChange={e => setTarget(e.target.value)} placeholder="@username" maxLength={80}/></label><button disabled={pending}>Создать приглашение</button></form>{inviteUrl ? <div className="notice"><p>Одноразовая ссылка, действует 7 дней:</p><input readOnly value={inviteUrl} onFocus={e => e.target.select()}/></div> : null}</section>
    <section className="panel"><h2>Мои уведомления</h2>{preferences.error ? <p className="danger">{(preferences.error as Error).message}</p> : Object.entries(preferences.data || {}).map(([event, checked]) => <label className="check-label" key={event}><input disabled={pending} type="checkbox" checked={checked} onChange={e => void act(() => api(`/projects/${project.id}/notifications`, 'PUT', { events: { ...preferences.data, [event]: e.target.checked } }))}/>{eventNames[event]}</label>)}</section>
    <section className="panel"><h2>GitHub</h2>{repositories.error ? <p className="danger">{(repositories.error as Error).message}</p> : repositories.data?.filter(r => !r.full_name.startsWith('disconnected/')).map(r => <div className="repo-row" key={r.id}><strong>{r.full_name}</strong><small>{r.webhook_url}</small><button disabled={pending} onClick={() => { if (window.confirm('Отключить репозиторий и удалить сохранённый токен?')) void act(() => api(`/projects/${project.id}/repositories/${r.id}`, 'DELETE')); }}>Отключить</button></div>)}<form onSubmit={e => { e.preventDefault(); void act(() => api<Repository>(`/projects/${project.id}/repositories`, 'POST', { full_name: repoName, token: token || null }), r => { setNewRepo(r); setToken(''); setRepoName(''); }); }}><label>Репозиторий<input required value={repoName} onChange={e => setRepoName(e.target.value)} placeholder="owner/repository"/></label><label>Fine-grained token для приватного репозитория<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} placeholder="Необязательно для публичного"/><small>Токен хранится в зашифрованном виде. Достаточно доступа к выбранному репозиторию.</small></label><button disabled={pending}>Подключить</button></form>{newRepo ? <div className="notice"><strong>Настройте webhook в GitHub</strong><label>Payload URL<input readOnly value={newRepo.webhook_url} onFocus={e => e.target.select()}/></label><label>Secret, показан только сейчас<input readOnly value={newRepo.webhook_secret} onFocus={e => e.target.select()}/></label><p>Content type: application/json. События: Push и Pull requests.</p></div> : null}</section>
    {user?.id === project.owner_id ? <section className="panel danger-zone"><h2>Владение и удаление</h2><label>Подтвердите ключ проекта<input value={confirmKey} onChange={e => setConfirmKey(e.target.value)} placeholder={project.key}/></label><label>Новый владелец<select value={newOwner} onChange={e => setNewOwner(Number(e.target.value))}><option value={0}>Выберите участника</option>{project.members.filter(m => m.id !== user.id).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label><button disabled={pending || !newOwner || confirmKey !== project.key} onClick={() => void act(() => api(`/projects/${project.id}/owner`, 'POST', { user_id: newOwner, confirm_key: confirmKey }))}>Передать владение</button><button className="danger-button" disabled={pending || confirmKey !== project.key} onClick={() => { if (window.confirm(`Удалить проект ${project.name} и скрыть все его задачи?`)) void act(() => api(`/projects/${project.id}`, 'DELETE', { confirm_key: confirmKey }), () => notice('Проект удалён. Вернитесь в обзор.')); }}>Удалить проект</button></section> : null}
  </>;
}
function StatusEditor({ status, save, pending, up, down }: { status: Status; save: (name: string, done: boolean) => void; pending: boolean; up?: () => void; down?: () => void }) { const [name, setName] = useState(status.name); const [done, setDone] = useState(status.is_done); useEffect(() => { setName(status.name); setDone(status.is_done); }, [status.name, status.is_done]); return <form className="status-editor" onSubmit={e => { e.preventDefault(); save(name, done); }}><input aria-label="Название статуса" required maxLength={80} value={name} onChange={e => setName(e.target.value)}/><label className="check-label"><input type="checkbox" checked={done} onChange={e => setDone(e.target.checked)}/>Готово</label><button className="icon-button" disabled={pending} aria-label="Сохранить статус"><Check size={16}/></button><button type="button" disabled={!up || pending} onClick={up} aria-label="Выше">↑</button><button type="button" disabled={!down || pending} onClick={down} aria-label="Ниже">↓</button></form>; }

createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={queryClient}><App/></QueryClientProvider></React.StrictMode>);
