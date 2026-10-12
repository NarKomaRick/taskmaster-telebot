export interface User { id: number; name: string; username?: string; telegram_id: number; }
export interface Status { id: number; name: string; position: number; is_done: boolean; }
export interface Project { id: number; key: string; name: string; description: string; owner_id: number; active_tasks?: number; statuses: Status[]; members: User[]; }
export interface Task { id: number; key: string; title: string; description: string; project_id: number; status_id: number; priority: string; assignee_id: number | null; deadline: string | null; sprint_id: number | null; version: number; deleted_at: string | null; checklist: Checklist[]; comments: Comment[]; attachments: Attachment[]; activity: Activity[]; time: TimeEntry[]; github: GitHubLink[]; total_seconds: number; }
export interface Checklist { id: number; text: string; done: boolean; }
export interface Comment { id: number; author_id: number; text: string; created_at: string; }
export interface Attachment { id: number; name: string; size: number; }
export interface Activity { id: number; kind: string; actor_id: number | null; task_id: number | null; data: Record<string, unknown>; created_at: string; }
export interface Sprint { id: number; name: string; state: string; start_date: string; end_date: string; result?: { total: number; done: number; }; }
export interface TimeEntry { id: number; task_id: number; user_id: number; seconds: number; elapsed_seconds: number; running_since: string | null; ended_at: string | null; started_at: string; task_key?: string; task_title?: string; project_name?: string; note: string; }
export interface GitHubLink { id: number; kind: string; title: string; url: string; state?: string; }
export interface Repository { id: number; full_name: string; webhook_url: string; webhook_secret?: string; }
export interface Dashboard { projects: Project[]; today: Task[]; overdue: Task[]; upcoming: Task[]; active_tasks: number; }
export interface TelegramApp { initData: string; colorScheme: string; ready(): void; expand(): void; BackButton: { show(): void; hide(): void; onClick(fn: () => void): void; offClick(fn: () => void): void; }; HapticFeedback?: { notificationOccurred(type: string): void; }; onEvent(event: string, callback: () => void): void; offEvent(event: string, callback: () => void): void; }
declare global { interface Window { Telegram?: { WebApp: TelegramApp; }; } }
