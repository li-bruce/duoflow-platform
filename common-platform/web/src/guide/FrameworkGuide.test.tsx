// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FrameworkGuide from './FrameworkGuide';
import SiteRoot from '../SiteRoot';
import { EXAMPLE_BODY, initialGuide, restoreGuide, STORAGE_KEY } from './guideState';

vi.mock('../App', () => ({ default: () => <div>实际工作台</div> }));
beforeEach(() => {
  localStorage.clear();
  history.replaceState(null, '', '/#guide');
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const stored = () => restoreGuide(localStorage.getItem(STORAGE_KEY));
const openPractice = () => fireEvent.click(screen.getByRole('button', { name: /亲手试一遍/ }));
const create = () => fireEvent.click(screen.getByRole('button', { name: '创建事项' }));
function reachReturn() {
  openPractice(); create();
  fireEvent.click(screen.getByRole('button', { name: '在对话中继续' }));
  fireEvent.click(screen.getByRole('button', { name: '提交事项' }));
  fireEvent.click(screen.getByRole('button', { name: '退回修改' }));
}

describe('interactive framework guide', () => {
  it('keeps the guide public and preserves direct workbench routes and browser navigation', async () => {
    const fetch = vi.spyOn(window, 'fetch');
    render(<SiteRoot/>);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('从头办到尾');
    expect(fetch).not.toHaveBeenCalled();
    for (const hash of ['#flows', '#devtools', '#chat', '#guide', '']) {
      await act(async () => { history.replaceState(null, '', '/' + hash); window.dispatchEvent(new HashChangeEvent('hashchange')); });
      if (['', '#guide'].includes(hash)) expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('从头办到尾');
      else expect(await screen.findByText('实际工作台')).toBeTruthy();
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it('completes one shared practice across page, conversation, reload and chapter navigation without replay', () => {
    let view = render(<FrameworkGuide/>);
    openPractice();
    fireEvent.change(screen.getByLabelText('事项名称'), { target: { value: '我们的分享会' } });
    create();
    expect(stored().history).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '在对话中继续' }));
    expect(stored().history).toHaveLength(1);
    expect(screen.getByRole('heading', { name: '我们的分享会' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '提交事项' }));
    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '请填写时间、地点' } });
    fireEvent.click(screen.getByRole('button', { name: '退回修改' }));
    expect(within(screen.getByRole('note', { name: '退回意见' })).getByText('请填写时间、地点')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('事项说明'), { target: { value: EXAMPLE_BODY } });
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    expect(stored().step).toBe(4);
    expect(screen.getByText('已退回')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /了解怎样配合/ }));
    expect(stored().history).toHaveLength(4);
    view.unmount(); view = render(<FrameworkGuide/>);
    openPractice();
    expect(screen.getByRole('heading', { name: '我们的分享会' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '再次提交' }));
    fireEvent.click(screen.getByRole('button', { name: '完成事项' }));
    const final = stored();
    expect(final.history.map(entry => entry.action)).toEqual(['创建事项', '提交事项', '退回修改', '保存修改', '再次提交', '完成事项']);
    expect(final.saved).toEqual({ title: '我们的分享会', body: EXAMPLE_BODY });
    fireEvent.click(screen.getByRole('button', { name: '在页面中' }));
    view.unmount(); render(<FrameworkGuide/>);
    expect(screen.getByText('已完成')).toBeTruthy();
    expect(stored().history).toEqual(final.history);
    expect(screen.queryByRole('button', { name: '提交事项' })).toBeNull();
  });

  it('explains missing inputs and requires an actual edit after return', () => {
    render(<FrameworkGuide/>); openPractice();
    fireEvent.change(screen.getByLabelText('事项名称'), { target: { value: ' ' } }); create();
    expect(screen.getByRole('alert').textContent).toBe('请填写事项名称。');
    expect(stored().history).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('事项名称'), { target: { value: '分享会' } }); create();
    fireEvent.click(screen.getByRole('button', { name: '在对话中继续' }));
    fireEvent.click(screen.getByRole('button', { name: '提交事项' }));
    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '退回修改' }));
    expect(stored().step).toBe(2);
    expect(screen.getByRole('alert').textContent).toBe('请填写处理说明。');
    fireEvent.change(screen.getByLabelText('退回原因'), { target: { value: '补充地点' } });
    fireEvent.click(screen.getByRole('button', { name: '退回修改' }));
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    expect(stored().history).toHaveLength(3);
    expect(screen.getByRole('alert').textContent).toContain('请根据退回意见修改说明');
  });

  it('confirms resetting the practice without clearing other browser data', () => {
    localStorage.setItem('unrelated-setting', 'keep');
    render(<FrameworkGuide/>); reachReturn();
    fireEvent.click(screen.getByRole('button', { name: '重新练习' }));
    fireEvent.click(screen.getByRole('button', { name: '保留当前练习' }));
    expect(stored().step).toBe(3);
    fireEvent.click(screen.getByRole('button', { name: '重新练习' }));
    fireEvent.click(screen.getByRole('button', { name: '清除并重新开始' }));
    expect(stored().step).toBe(0);
    expect(localStorage.getItem('unrelated-setting')).toBe('keep');
  });

  it('recovers from broken storage and remains usable when saving is blocked', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...initialGuide(), step: 6 }));
    render(<FrameworkGuide/>); openPractice();
    expect(screen.getByRole('button', { name: '创建事项' })).toBeTruthy();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    create();
    expect(screen.getByRole('status').textContent).toContain('浏览器暂时无法保存练习');
    expect(screen.getByRole('button', { name: '在对话中继续' })).toBeTruthy();
  });

  it('switches concrete examples, reveals development steps and copies the selected explanation', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    try {
      render(<FrameworkGuide/>);
      fireEvent.click(screen.getByRole('button', { name: /接入自己的功能/ }));
      fireEvent.click(screen.getByRole('button', { name: '设备报修' }));
      expect(screen.getByText('设备名称、所在位置、故障说明')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: '复制这份说明' }));
      expect(await screen.findByText('已复制这份说明')).toBeTruthy();
      expect(writeText).toHaveBeenCalledWith(expect.stringContaining('办理对象：一张报修单'));
      const details = screen.getByText('准备开发时，具体怎么接入？').closest('details')!;
      fireEvent.click(within(details).getByRole('button', { name: '填一次，就能得到结果', hidden: true }));
      expect(screen.getByText(/例如文字统计、数据计算/)).toBeTruthy();
      expect(screen.getByRole('link', { name: '打开实际工作台' }).getAttribute('href')).toBe('#flows');
    } finally { vi.unstubAllGlobals(); }
  });
});
