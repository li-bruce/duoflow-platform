export type Surface = 'page' | 'conversation';
export type Chapter = 0 | 1 | 2 | 3;
export type Step = 0 | 1 | 2 | 3 | 4 | 5 | 6;
export type Entry = { action: string; title: string; body: string; note: string };
export type GuideState = {
  version: 1;
  chapter: Chapter;
  surface: Surface;
  step: Step;
  title: string;
  body: string;
  note: string;
  saved: { title: string; body: string } | null;
  feedback: string;
  history: Entry[];
};
export type GuideAction =
  | { type: 'chapter'; chapter: Chapter }
  | { type: 'surface'; surface: Surface }
  | { type: 'field'; field: 'title' | 'body' | 'note'; value: string }
  | { type: 'operate' }
  | { type: 'restart' };

export const STORAGE_KEY = 'duo-framework-guide-v1';
export const ACTIONS = ['创建事项', '提交事项', '退回修改', '保存修改', '再次提交', '完成事项'];
export const STATUS = ['尚未创建', '草稿', '已提交', '已退回', '已退回', '已提交', '已完成'];
export const EXAMPLE_BODY = '邀请同事分享最近读过的一本书。\n时间：周五下午 3 点。\n地点：二楼会议室。';

export function initialGuide(): GuideState {
  return {
    version: 1, chapter: 0, surface: 'page', step: 0,
    title: '准备一次读书分享会', body: '邀请同事分享最近读过的一本书。',
    note: '请补充分享会的时间和地点。', saved: null, feedback: '', history: [],
  };
}

export function operationError(state: GuideState): string {
  if (state.step === 0 || state.step === 3) {
    if (!state.title.trim()) return '请填写事项名称。';
    if (!state.body.trim()) return '请填写事项说明。';
    if (state.step === 3 && state.title.trim() === state.saved?.title && state.body.trim() === state.saved?.body)
      return '请根据退回意见修改说明，再保存。';
  }
  if ((state.step === 2 || state.step === 5) && !state.note.trim()) return '请填写处理说明。';
  return '';
}

export function guideReducer(state: GuideState, action: GuideAction): GuideState {
  if (action.type === 'restart') return { ...initialGuide(), chapter: 1 };
  if (action.type === 'chapter') return { ...state, chapter: action.chapter };
  if (action.type === 'surface') return { ...state, surface: action.surface };
  if (action.type === 'field') {
    if (action.field === 'note' ? ![2, 5].includes(state.step) : ![0, 3].includes(state.step)) return state;
    const limits = { title: 200, body: 4000, note: 1000 };
    return { ...state, [action.field]: action.value.slice(0, limits[action.field]) };
  }
  if (state.step === 6 || operationError(state) || (state.step === 1 && state.surface !== 'conversation')) return state;
  const saved = [0, 3].includes(state.step) ? { title: state.title.trim(), body: state.body.trim() } : state.saved!;
  return {
    ...state, saved, step: (state.step + 1) as Step,
    feedback: state.step === 2 ? state.note.trim() : state.feedback,
    note: state.step === 4 ? '内容已确认，准备完成。' : state.note,
    history: [...state.history, {
      action: ACTIONS[state.step], title: saved.title, body: saved.body,
      note: [2, 5].includes(state.step) ? state.note.trim() : '',
    }],
  };
}

const textWithin = (value: unknown, max: number): value is string => typeof value === 'string' && value.length <= max;

/** Only this guide's small, versioned practice record is restored. */
export function restoreGuide(raw: string | null): GuideState {
  if (!raw || raw.length > 60000) return initialGuide();
  try {
    const value = JSON.parse(raw);
    if (value.version !== 1 || ![0, 1, 2, 3].includes(value.chapter) ||
        !['page', 'conversation'].includes(value.surface) || !Number.isInteger(value.step) || value.step < 0 || value.step > 6 ||
        !textWithin(value.title, 200) || !textWithin(value.body, 4000) || !textWithin(value.note, 1000) || !textWithin(value.feedback, 1000) ||
        !Array.isArray(value.history) || value.history.length !== value.step ||
        !value.history.every((entry: Entry, index: number) => entry && entry.action === ACTIONS[index] &&
          textWithin(entry.title, 200) && textWithin(entry.body, 4000) && textWithin(entry.note, 1000))) return initialGuide();
    const last = value.history.at(-1);
    if (value.step === 0 ? value.saved !== null : !value.saved || !last ||
        value.saved.title !== last.title || value.saved.body !== last.body) return initialGuide();
    return {
      version: 1, chapter: value.chapter, surface: value.surface, step: value.step,
      title: value.title, body: value.body, note: value.note, feedback: value.feedback,
      saved: value.saved && { title: value.saved.title, body: value.saved.body },
      history: value.history.map((entry: Entry) => ({ action: entry.action, title: entry.title, body: entry.body, note: entry.note })),
    };
  } catch { return initialGuide(); }
}
