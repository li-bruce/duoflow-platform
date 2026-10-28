// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import ModuleAISettings from './ModuleAISettings';
import type {ModuleAIService} from './ModuleAISettings';
import {api, ApiError, post} from './api';
import type {ModelProfile, User} from './types';

vi.mock('./api', async original => ({...await original<typeof import('./api')>(), api: vi.fn(), post: vi.fn()}));
const admin: User = {id: 'admin-a', username: 'synthetic-admin', name: '合成管理员', role: 'member', roles: ['system_admin'], authorization_revision: 1};
const profile: ModelProfile = {id: 'profile-a', name: '合成平台连接', base_url: 'https://synthetic.example/v1', model: 'synthetic-model', enabled: true, api_key_configured: true, temperature: 0.3, max_tokens: 2048, timeout_seconds: 60, is_default: true};
const service: ModuleAIService = {id: 'extract', title: '合成提取服务', description: '仅用于配置测试', capability_ids: ['synthetic.extract'], supported_sources: ['platform', 'module'], module_id: 'synthetic', remote_service_id: 'synthetic-remote', remote_revision: 3, remote_enabled: true, binding_stale: false, source: 'platform', platform_profile_id: 'profile-a', revision: 2};
type Configuration = {revision: number; profile: ModelProfile | null};
let rows: ModuleAIService[];
let configuration: Configuration;
const root = '/ai-services/synthetic/extract';
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (value: unknown) => void; const promise = new Promise<T>((yes, no) => {resolve = yes; reject = no;}); return {resolve, reject, promise}; }
beforeEach(() => {
  vi.clearAllMocks(); rows = [structuredClone(service)]; configuration = {revision: 4, profile: {...profile, id: 'module-profile', name: '模块原有连接', model: 'module-model', is_default: false}};
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path === '/ai-services') return {items: structuredClone(rows)};
    if (path === `${root}/module-config` && !options?.method) return structuredClone(configuration);
    if (path === `${root}/module-config` && options?.method === 'PUT') {
      const body = JSON.parse(String(options.body));
      configuration = {revision: configuration.revision + 1, profile: {...configuration.profile, ...body.profile, id: 'module-profile', is_default: false, api_key_configured: body.profile.clear_api_key ? false : !!body.profile.api_key || !!configuration.profile?.api_key_configured}};
      const safe = structuredClone(configuration);
      if (safe.profile) { delete (safe.profile as unknown as Record<string, unknown>).api_key; delete (safe.profile as unknown as Record<string, unknown>).clear_api_key; }
      return safe;
    }
    if (path === `${root}/binding`) {
      const body = JSON.parse(String(options?.body)); rows = [{...rows[0], source: body.source, platform_profile_id: body.platform_profile_id, revision: rows[0].revision + 1, binding_stale: false}];
      return structuredClone(rows[0]);
    }
    throw new Error(`Unexpected request ${path}`);
  });
  vi.mocked(post).mockResolvedValue({service_id: 'extract', source: 'platform', binding_revision: 2, module_config_revision: null, platform_config_digest: 'a'.repeat(64), result: {ok: true, message: '合成连接可用', latency_ms: 12, model: 'synthetic-model', response_preview: 'synthetic reply'}});
});
afterEach(cleanup);
async function open() { const view = render(<ModuleAISettings user={admin} profiles={[profile]}/>); await screen.findByRole('region', {name: '合成提取服务 · 模块 AI 服务'}); return view; }
async function openModule() { await open(); fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'module'}}); await screen.findByLabelText('模块 API Key'); }

describe('模块 AI 来源配置', () => {
  it('只展示已声明的服务，普通用户不发管理请求', async () => {
    const view = render(<ModuleAISettings user={{...admin, roles: []}} profiles={[profile]}/>);
    expect(screen.getByText('模块 AI 配置由管理员维护')).toBeInTheDocument(); expect(api).not.toHaveBeenCalled();
    view.rerender(<ModuleAISettings user={admin} profiles={[profile]}/>); await screen.findByText('合成提取服务');
    expect(screen.getAllByRole('region', {name: /· 模块 AI 服务/})).toHaveLength(1);
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('已保存来源：主框架 API');
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('无服务声明时显示真实空状态，不补造模块配置', async () => {
    rows = []; await openEmpty();
    expect(screen.queryByLabelText('AI 调用来源')).toBeNull();
    expect(screen.getByText('尚无模块声明 AI 服务')).toBeInTheDocument();
  });

  it('模块 API 配置与来源分开保存，密钥不回显且保存后立即清空', async () => {
    await openModule();
    const key = screen.getByLabelText('模块 API Key');
    expect(key).toHaveAttribute('type', 'password'); expect(key).toHaveValue('');
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('主框架 API');
    fireEvent.change(key, {target: {value: 'synthetic-secret-only'}});
    fireEvent.change(screen.getByLabelText('模块模型标识'), {target: {value: 'new-module-model'}});
    expect(screen.getByRole('button', {name: '保存 AI 来源'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '保存模块 API 配置'}));
    await screen.findByText(/模块 API 配置已保存/);
    expect(key).toHaveValue('');
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('主框架 API');
    const saved = vi.mocked(api).mock.calls.find(([path, options]) => path === `${root}/module-config` && options?.method === 'PUT');
    expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({expected_revision: 4, profile: {api_key: 'synthetic-secret-only', model: 'new-module-model'}});
    fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    await waitFor(() => expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('模块自身 API'));
    const binding = vi.mocked(api).mock.calls.find(([path]) => path === `${root}/binding`);
    expect(JSON.parse(String(binding?.[1]?.body))).toEqual({expected_revision: 2, source: 'module', platform_profile_id: null});
    expect(screen.getByRole('button', {name: '测试已保存的 AI 来源'})).toBeEnabled();
  });

  it('留空时不发送 api_key，显式清除密钥必须提交 clear_api_key', async () => {
    await openModule(); fireEvent.click(screen.getByRole('button', {name: '保存模块 API 配置'}));
    await screen.findByText(/模块 API 配置已保存/);
    let call = vi.mocked(api).mock.calls.filter(([path, options]) => path === `${root}/module-config` && options?.method === 'PUT').at(-1);
    expect(JSON.parse(String(call?.[1]?.body)).profile).not.toHaveProperty('api_key');
    fireEvent.click(screen.getByLabelText('保存时移除模块已有密钥')); fireEvent.click(screen.getByRole('button', {name: '保存模块 API 配置'}));
    await screen.findByText('模块尚未配置密钥。 留空保留已有密钥。');
    call = vi.mocked(api).mock.calls.filter(([path, options]) => path === `${root}/module-config` && options?.method === 'PUT').at(-1);
    expect(JSON.parse(String(call?.[1]?.body)).profile).toMatchObject({clear_api_key: true});
  });

  it('保存失败也清空密钥，其余编辑值保留', async () => {
    await openModule(); fireEvent.change(screen.getByLabelText('模块 API Key'), {target: {value: 'synthetic-secret-only'}});
    fireEvent.change(screen.getByLabelText('模块模型标识'), {target: {value: 'uncommitted-model'}});
    vi.mocked(api).mockRejectedValueOnce(new ApiError('module_offline', '模块暂时不可用', 503));
    fireEvent.click(screen.getByRole('button', {name: '保存模块 API 配置'}));
    await screen.findByText('模块暂时不可用');
    expect(screen.getByLabelText('模块 API Key')).toHaveValue(''); expect(screen.getByLabelText('模块模型标识')).toHaveValue('uncommitted-model');
    expect(screen.getByRole('button', {name: '测试已保存的 AI 来源'})).toBeDisabled();
  });

  it('平台来源必须明确选择一个启用连接，默认连接不会代选', async () => {
    rows = [{...service, source: 'disabled', platform_profile_id: null}]; await open();
    fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'platform'}});
    expect(screen.getByLabelText('主框架模型连接')).toHaveValue('');
    expect(screen.getByRole('button', {name: '保存 AI 来源'})).toBeDisabled();
    fireEvent.change(screen.getByLabelText('主框架模型连接'), {target: {value: 'profile-a'}});
    fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    await waitFor(() => expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('主框架 API'));
    const binding = vi.mocked(api).mock.calls.find(([path]) => path === `${root}/binding`);
    expect(JSON.parse(String(binding?.[1]?.body))).toEqual({expected_revision: 2, source: 'platform', platform_profile_id: 'profile-a'});
  });

  it('连接测试只测试已保存来源并显示来源与修订，不声称业务完成', async () => {
    await open(); fireEvent.click(screen.getByRole('button', {name: '测试已保存的 AI 来源'}));
    await screen.findByText('AI 连接测试成功');
    expect(post).toHaveBeenCalledWith(`${root}/test`, {expected_revision: 2});
    expect(screen.getByText(/来源修订 2 · synthetic-model。测试使用合成文本，不代表业务已完成。/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'disabled'}});
    expect(screen.queryByText('AI 连接测试成功')).toBeNull();
    expect(screen.getByRole('button', {name: '测试已保存的 AI 来源'})).toBeDisabled();
  });

  it('来源 409 后保留草稿并更新正式事实，明确放弃后才使用新修订', async () => {
    await open(); fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'disabled'}});
    rows = [{...service, revision: 3}];
    vi.mocked(api).mockRejectedValueOnce(new ApiError('ai_revision_conflict', '来源配置已由其他管理员修改', 409));
    fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    await screen.findByText('来源配置已由其他管理员修改');
    await screen.findByText(/正在编辑来源修订 2，服务器当前为修订 3/);
    expect(screen.getByLabelText('AI 调用来源')).toHaveValue('disabled');
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('主框架 API');
    expect(screen.getByRole('button', {name: '保存 AI 来源'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '载入最新来源并放弃修改'}));
    expect(screen.getByLabelText('AI 调用来源')).toHaveValue('platform');
    fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'disabled'}});
    fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    await waitFor(() => expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('未启用 AI 服务'));
    const call = vi.mocked(api).mock.calls.filter(([path]) => path === `${root}/binding`).at(-1);
    expect(JSON.parse(String(call?.[1]?.body)).expected_revision).toBe(3);
  });

  it('配置 409 后保留非密钥草稿并要求重新读取，不覆盖服务器事实', async () => {
    await openModule(); fireEvent.change(screen.getByLabelText('模块模型标识'), {target: {value: 'my-draft'}});
    fireEvent.change(screen.getByLabelText('模块 API Key'), {target: {value: 'synthetic-secret-only'}});
    configuration = {revision: 5, profile: {...profile, model: 'server-new-model'}};
    vi.mocked(api).mockRejectedValueOnce(new ApiError('ai_revision_conflict', '模块配置修订已变化', 409));
    fireEvent.click(screen.getByRole('button', {name: '保存模块 API 配置'}));
    await screen.findByText(/模块配置已变化，草稿已保留/);
    expect(screen.getByLabelText('模块模型标识')).toHaveValue('my-draft'); expect(screen.getByLabelText('模块 API Key')).toHaveValue('');
    expect(screen.getByText('配置修订 5')).toBeInTheDocument(); expect(screen.getByRole('button', {name: '保存模块 API 配置'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '放弃模块修改并重新读取'}));
    await waitFor(() => expect(screen.getByLabelText('模块模型标识')).toHaveValue('server-new-model'));
    expect(screen.getByRole('button', {name: '保存模块 API 配置'})).toBeEnabled();
  });

  it('单个模块离线时保留总列表及平台来源，不自动切换', async () => {
    await open(); vi.mocked(api).mockRejectedValueOnce(new ApiError('module_offline', '模块配置暂不可读', 503));
    fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'module'}});
    await screen.findByText('模块配置暂不可读');
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('主框架 API');
    expect(screen.getByRole('button', {name: '保存 AI 来源'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '刷新服务列表'})).toBeEnabled(); expect(post).not.toHaveBeenCalled();
  });

  it('来源保存开始前发出的旧列表响应不能逆转保存结果', async () => {
    await open(); const delayed = deferred<{items: ModuleAIService[]}>(); vi.mocked(api).mockReturnValueOnce(delayed.promise);
    fireEvent.click(screen.getByRole('button', {name: '刷新服务列表'}));
    fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'disabled'}});
    fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    await waitFor(() => expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('未启用 AI 服务'));
    await act(async () => delayed.resolve({items: [service]}));
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('未启用 AI 服务');
  });

  it('管理员权限撤销后忽略晚到列表，重新获得权限时重新读取', async () => {
    const delayed = deferred<{items: ModuleAIService[]}>(); vi.mocked(api).mockReturnValueOnce(delayed.promise);
    const view = render(<ModuleAISettings user={admin} profiles={[profile]}/>);
    view.rerender(<ModuleAISettings user={{...admin, roles: [], authorization_revision: 2}} profiles={[profile]}/>);
    await act(async () => delayed.resolve({items: [service]}));
    expect(screen.queryByText('合成提取服务')).toBeNull(); expect(screen.getByText('模块 AI 配置由管理员维护')).toBeInTheDocument();
    view.rerender(<ModuleAISettings user={{...admin, authorization_revision: 3}} profiles={[profile]}/>);
    await screen.findByText('合成提取服务'); expect(api).toHaveBeenCalledTimes(2);
  });

  it('管理员账号切换后丢弃旧保存结果，保留新账号读取的事实', async () => {
    const view = await open(); const delayed = deferred<ModuleAIService>(); vi.mocked(api).mockReturnValueOnce(delayed.promise);
    fireEvent.change(screen.getByLabelText('AI 调用来源'), {target: {value: 'disabled'}}); fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    rows = [{...service, title: '新账号服务'}];
    view.rerender(<ModuleAISettings user={{...admin, id: 'admin-b'}} profiles={[profile]}/>);
    await screen.findByText('新账号服务'); await act(async () => delayed.resolve({...service, source: 'disabled', revision: 3}));
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('主框架 API'); expect(screen.queryByText('AI 来源已保存。后续调用使用该来源；不会自动切换到另一来源。')).toBeNull();
  });

  it('连接或声明变化使旧绑定失效，清除旧测试结果并要求显式重新保存来源', async () => {
    await open(); fireEvent.click(screen.getByRole('button', {name: '测试已保存的 AI 来源'}));
    await screen.findByText('AI 连接测试成功');
    rows = [{...service, remote_revision: 4, binding_stale: true}];
    fireEvent.click(screen.getByRole('button', {name: '刷新服务列表'}));
    await screen.findByText(/模块连接或声明已变化，已保存来源暂不可用/);
    expect(screen.queryByText('AI 连接测试成功')).toBeNull();
    expect(screen.getByRole('button', {name: '测试已保存的 AI 来源'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '保存 AI 来源'}));
    await waitFor(() => expect(screen.queryByText(/模块连接或声明已变化，已保存来源暂不可用/)).toBeNull());
    expect(screen.getByRole('button', {name: '测试已保存的 AI 来源'})).toBeEnabled();
  });

  it('停用模块仍显示已保存事实，但禁用来源保存和测试，不请求模块配置', async () => {
    rows = [{...service, remote_enabled: false, source: 'module', platform_profile_id: null}];
    await open();
    expect(screen.getByLabelText('已保存的 AI 来源')).toHaveTextContent('模块自身 API');
    expect(screen.getByText(/模块已停用，当前 AI 来源不可使用/)).toBeInTheDocument();
    expect(screen.getByLabelText('AI 调用来源')).toBeDisabled();
    expect(screen.getByRole('button', {name: '保存 AI 来源'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '测试已保存的 AI 来源'})).toBeDisabled();
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('模块连接修订变化时保留配置草稿，但重新读取前不能把旧连接配置写入新连接', async () => {
    await openModule();
    fireEvent.change(screen.getByLabelText('模块模型标识'), {target: {value: 'my-old-connection-draft'}});
    rows = [{...service, remote_revision: 4, binding_stale: true}];
    fireEvent.click(screen.getByRole('button', {name: '刷新服务列表'}));
    await screen.findByText(/模块连接已变化，下方配置来自旧连接/);
    expect(screen.getByLabelText('模块模型标识')).toHaveValue('my-old-connection-draft');
    expect(screen.getByRole('button', {name: '保存模块 API 配置'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '保存 AI 来源'})).toBeDisabled();
    configuration = {revision: 7, profile: {...profile, model: 'new-connection-model'}};
    fireEvent.click(screen.getByRole('button', {name: '放弃模块修改并重新读取'}));
    await waitFor(() => expect(screen.getByLabelText('模块模型标识')).toHaveValue('new-connection-model'));
    expect(screen.getByRole('button', {name: '保存模块 API 配置'})).toBeEnabled();
  });

  it('接收契约允许的大 token 与小数超时配置，修改密钥不会被浏览器旧限额阻断', async () => {
    configuration = {revision: 4, profile: {...profile, max_tokens: 65536, timeout_seconds: 1.5, temperature: 0.25}};
    await openModule();
    expect(screen.getByLabelText('模块最大输出 Token')).toBeValid();
    expect(screen.getByLabelText('模块超时（秒）')).toBeValid();
    expect(screen.getByLabelText('模块 Temperature')).toBeValid();
    fireEvent.change(screen.getByLabelText('模块 API Key'), {target: {value: 'synthetic-new-secret'}});
    fireEvent.click(screen.getByRole('button', {name: '保存模块 API 配置'}));
    await screen.findByText(/模块 API 配置已保存/);
    const call = vi.mocked(api).mock.calls.find(([path, options]) => path === `${root}/module-config` && options?.method === 'PUT');
    expect(JSON.parse(String(call?.[1]?.body)).profile).toMatchObject({max_tokens: 65536, timeout_seconds: 1.5, temperature: 0.25, api_key: 'synthetic-new-secret'});
  });

  it('模块来源的连接测试同时标明实际来源修订和模块配置修订', async () => {
    rows = [{...service, source: 'module', platform_profile_id: null}];
    vi.mocked(post).mockResolvedValue({service_id: 'extract', source: 'module', binding_revision: 2, module_config_revision: 4, platform_config_digest: null, result: {ok: true, message: '合成模块连接可用', latency_ms: 8, model: 'module-model'}});
    await open(); await screen.findByLabelText('模块 API Key');
    fireEvent.click(screen.getByRole('button', {name: '测试已保存的 AI 来源'}));
    await screen.findByText('AI 连接测试成功');
    expect(screen.getByText(/来源修订 2 · 模块配置修订 4 · module-model/)).toBeInTheDocument();
  });
});

async function openEmpty() { render(<ModuleAISettings user={admin} profiles={[profile]}/>); await screen.findByText('尚无模块声明 AI 服务'); }
