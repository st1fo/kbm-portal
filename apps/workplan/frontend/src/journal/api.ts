import { api } from '../api';

export type Status = 'out' | 'in' | 'break' | 'done';
export type PunchKind = 'in' | 'break' | 'out' | 'undo';
export type Segment = { start: string; end: string | null; start_next_day?: boolean; end_next_day?: boolean };
export type Day = {
  date: string; done: boolean; status: Status; segments: Segment[]; worked_min: number; break_min: number; count: number;
  open: boolean; break_running: boolean; first: string | null; last: string | null; last_next_day: boolean;
};
export type JState = { server_now: string; active_day: string; status: Status; day: Day; pending?: number; skew_min?: number };
export type OverviewDay = Day & { weekend: boolean; holiday: string | null; logged_min: number };
export type Overview = {
  month: string; is_current: boolean; days: OverviewDay[];
  totals: { worked_min: number; logged_min: number; worked_days: number; avg_closed_min: number | null };
  today: { date: string; worked_min: number; logged_min: number } | null;
  week: { worked_min: number; logged_min: number } | null;
};
export type Customer = { id: number; name: string; active: boolean };
export type Project = { id: number; name: string; customer_id: number | null; customer: string; active: boolean; created_by: number | null };
export type Task = {
  id: number; name: string; project_id: number | null; project: string; customer_id: number | null; customer: string;
  done: boolean; active: boolean; created_by: number | null; activity_count: number; minutes: number;
};
export type Activity = {
  id: number; user_id: number; task_id: number | null; task: string; task_done: boolean; project_id: number | null; project: string;
  customer: string; date: string; start: string; end: string | null; end_next_day: boolean; minutes: number | null; text: string;
  start_at: string; end_at: string | null;
};
export type Todo = {
  id: number; user_id: number; text: string; due: string | null; priority: number; project_id: number | null; project: string;
  project_customer: string; customer_id: number | null; customer: string; done: boolean; done_at: string | null; created_at: string;
};
export type ProjectStat = { project_id: number | null; project: string; customer: string; minutes: number; count: number };
export type ImportCounts = Record<'days' | 'segments' | 'customers' | 'projects' | 'tasks' | 'activities' | 'todos', number>;
export type ImportResult = { dry_run: boolean; created: ImportCounts; skipped: ImportCounts; warnings: string[] };
/** Priradenie to-do: projekt, existujúci zákazník alebo nový zákazník podľa názvu. */
export type Assign = { project_id?: number | null; customer_id?: number | null; customer_name?: string | null };

/** user_id do query: null = prihlásený používateľ (admin môže prezerať iného zamestnanca). */
export const withUser = (path: string, uid: number | null) => uid ? path + (path.includes('?') ? '&' : '?') + 'user_id=' + uid : path;
const j = (path: string) => '/journal' + path;

export const journalApi = {
  state: (uid: number | null) => api<JState>(withUser(j('/state'), uid)),
  day: (date: string, uid: number | null) => api<Day>(withUser(j('/days/' + date), uid)),
  saveDay: (date: string, segments: { start: string | null; end: string | null }[], uid: number | null) =>
    api<Day>(withUser(j('/days/' + date), uid), 'PUT', { segments }),
  deleteDay: (date: string, uid: number | null) => api(withUser(j('/days/' + date), uid), 'DELETE'),
  overview: (month: string, uid: number | null) => api<Overview>(withUser(j('/overview?month=' + month), uid)),
  projectStats: (range: { start: string; end: string } | null, uid: number | null) =>
    api<ProjectStat[]>(withUser(j('/project-stats' + (range ? `?start=${range.start}&end=${range.end}` : '')), uid)),
  customers: () => api<Customer[]>(j('/customers')),
  projects: (all = false) => api<Project[]>(j('/projects' + (all ? '?include_inactive=true' : ''))),
  addProject: (name: string, customer_name: string) => api<Project>(j('/projects'), 'POST', { name, customer_name: customer_name || null }),
  editProject: (id: number, data: Partial<{ name: string; active: boolean; customer_name: string | null }>) => api<Project>(j('/projects/' + id), 'PATCH', data),
  deleteProject: (id: number) => api(j('/projects/' + id), 'DELETE'),
  tasks: (uid: number | null, all = false) => api<Task[]>(withUser(j('/tasks' + (all ? '?include_inactive=true' : '')), uid)),
  addTask: (data: { name: string; project_id: number | null; customer_name?: string | null }) => api<Task>(j('/tasks'), 'POST', data),
  editTask: (id: number, data: Partial<{ name: string; done: boolean; active: boolean; project_id: number | null; customer_name: string | null }>) =>
    api<Task>(j('/tasks/' + id), 'PATCH', data),
  deleteTask: (id: number) => api(j('/tasks/' + id), 'DELETE'),
  activities: (params: Record<string, string | number | undefined>, uid: number | null) => {
    const q = Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join('&');
    return api<Activity[]>(withUser(j('/activities' + (q ? '?' + q : '')), uid));
  },
  addActivity: (data: ActivityInput, uid: number | null) => api<Activity>(withUser(j('/activities'), uid), 'POST', data),
  editActivity: (id: number, data: ActivityInput) => api<Activity>(j('/activities/' + id), 'PUT', data),
  deleteActivity: (id: number) => api(j('/activities/' + id), 'DELETE'),
  todos: (uid: number | null) => api<Todo[]>(withUser(j('/todos'), uid)),
  addTodo: (data: { text: string; due: string | null; priority: number } & Assign, uid: number | null) => api<Todo>(withUser(j('/todos'), uid), 'POST', data),
  editTodo: (id: number, data: Partial<{ text: string; due: string | null; priority: number; done: boolean }> & Assign) => api<Todo>(j('/todos/' + id), 'PATCH', data),
  deleteTodo: (id: number) => api(j('/todos/' + id), 'DELETE'),
  clearDoneTodos: (uid: number | null) => api<{ deleted: number }>(withUser(j('/todos/clear-done'), uid), 'POST'),
  importLegacy: (payload: unknown, dryRun: boolean) => api<ImportResult>(j('/import?dry_run=' + dryRun), 'POST', payload),
};
export type ActivityInput = { task_id: number; date: string; start: string | null; end: string | null; text: string };
