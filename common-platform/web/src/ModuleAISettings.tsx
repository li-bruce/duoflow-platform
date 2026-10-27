import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, CircleDot, FlaskConical, RefreshCw, Save, Server } from 'lucide-react';
import { api, ApiError, post } from './api';
import { isPlatformAdmin } from './types';
import type { ModelProfile, TestResult, User } from './types';
import { Empty, ErrorNotice, Loading, Success } from './ui';
import './ModuleAISettings.css';

export type AIServiceSource = 'disabled' | 'platform' | 'module';
export type ModuleAIService = {
  id: string; title: string; description: string; capability_ids: string[];
  supported_sources: ('platform' | 'module')[]; module_id: string;
  remote_service_id: string | null; remote_revision: number | null;
  remote_enabled: boolean; binding_stale: boolean;
  source: AIServiceSource; platform_profile_id: string | null; revision: number;
};
type ModuleConfiguration = {revision: number; profile: ModelProfile | null};
type ProfileForm = Omit<ModelProfile, 'id' | 'api_key_configured' | 'is_default'> & {api_key: string; clear_api_key: boolean};
type ServiceTest = {service_id: string; source: AIServiceSource; binding_revision: number; module_config_revision: number | null; platform_config_digest: string | null; result: TestResult};
const emptyProfile = (title: string): ProfileForm => ({name: title, base_url: '', model: '', enabled: true, temperature: 0.3, max_tokens: 2048, timeout_seconds: 60, api_key: '', clear_api_key: false});
const profileForm = (value: ModelProfile | null, title: string): ProfileForm => value
  ? {name: value.name, base_url: value.base_url, model: value.model, enabled: value.enabled, temperature: value.temperature, max_tokens: value.max_tokens, timeout_seconds: value.timeout_seconds, api_key: '', clear_api_key: false}
  : emptyProfile(title);
const sourceName = (source: AIServiceSource) => ({disabled: '未启用 AI 服务', platform: '主框架 API', module: '模块自身 API'})[source];
const revisionConflict = (error: unknown) => error instanceof ApiError && error.status === 409;

export default function ModuleAISettings({user, profiles}: {user: User; profiles: ModelProfile[]}) {
  const allowed = isPlatformAdmin(user);
  const identity = `${user.id}:${user.authorization_revision ?? 0}:${allowed}`;
  const [items, setItems] = useState<ModuleAIService[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>();
  const epoch = useRef(0);
  const sequence = useRef(0);
  const load = useCallback(async () => {
    if (!allowed) return;
    const owner = epoch.current;
    const request = ++sequence.current;
    setLoading(true); setError(null);
    try {
      const value = await api<{items: ModuleAIService[]}>('/ai-services');
      if (owner === epoch.current && request === sequence.current) setItems(value.items);
    } catch (failure) {
      if (owner === epoch.current && request === sequence.current) setError(failure);
    } finally {
      if (owner === epoch.current && request === sequence.current) setLoading(false);
    }
  }, [allowed, identity]);
  useEffect(() => {
    ++epoch.current; setItems([]); setError(null); setLoading(false);
    if (allowed) void load();
    return () => { ++epoch.current; ++sequence.current; };
  }, [identity, allowed, load]);
  if (!allowed) return <Empty title="模块 AI 配置由管理员维护" description="模块声明 AI 服务后，管理员可以配置调用来源。"/>;
  return <section className="module-ai-settings" aria-label="模块 AI 服务配置">
    <div className="module-ai-heading"><div><h2>模块 AI 服务</h2><p>为模块明确选择主框架 API 或模块自身 API。配置与来源分别保存；连接测试只检查已保存来源。</p></div><button type="button" className="button secondary small" onClick={() => void load()} disabled={loading}><RefreshCw size={14}/>刷新服务列表</button></div>
    <ErrorNotice error={error}/>
    {loading && !items.length ? <Loading label="正在读取模块 AI 声明…"/> : items.length ? <div className="module-ai-list">{items.map(item => <ServiceSettings key={`${identity}:${item.module_id}:${item.id}`} item={item} profiles={profiles} refresh={load} onSaved={value => { ++sequence.current; setLoading(false); setItems(previous => previous.map(row => row.module_id === value.module_id && row.id === value.id ? value : row)); }}/>)}</div> : !error && <Empty title="尚无模块声明 AI 服务" description="模块须先在公开契约中声明 AI 服务，接入后才能在这里配置。"/>}
  </section>;
}

function ServiceSettings({item, profiles, refresh, onSaved}: {item: ModuleAIService; profiles: ModelProfile[]; refresh: () => Promise<void>; onSaved: (value: ModuleAIService) => void}) {
  const path = `/ai-services/${encodeURIComponent(item.module_id)}/${encodeURIComponent(item.id)}`;
  const [source, setSource] = useState<AIServiceSource>(item.source);
  const [profileId, setProfileId] = useState(item.platform_profile_id || '');
  const [bindingRevision, setBindingRevision] = useState(item.revision);
  const [bindingDirty, setBindingDirty] = useState(false);
  const [bindingConflict, setBindingConflict] = useState(false);
  const [configuration, setConfiguration] = useState<ModuleConfiguration | null>(null);
  const [configurationRemoteRevision, setConfigurationRemoteRevision] = useState<number | null | undefined>();
  const [configurationRevision, setConfigurationRevision] = useState<number | null>(null);
  const [form, setForm] = useState<ProfileForm>(() => emptyProfile(item.title));
  const [configurationDirty, setConfigurationDirty] = useState(false);
  const [configurationConflict, setConfigurationConflict] = useState(false);
  const [configurationOpen, setConfigurationOpen] = useState(item.source === 'module');
  const [configurationLoading, setConfigurationLoading] = useState(false);
  const [configurationError, setConfigurationError] = useState<unknown>();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState('');
  const [test, setTest] = useState<ServiceTest | null>(null);
  const alive = useRef(true);
  const configurationSequence = useRef(0);
  useEffect(() => { alive.current = true; return () => { alive.current = false; ++configurationSequence.current; }; }, []);
  useEffect(() => {
    if (!bindingDirty) {
      setSource(item.source); setProfileId(item.platform_profile_id || ''); setBindingRevision(item.revision);
    }
    setTest(null);
  }, [item.source, item.platform_profile_id, item.revision, item.remote_enabled, item.binding_stale, item.remote_revision, bindingDirty]);
  const readConfiguration = useCallback(async (discard = true) => {
    const request = ++configurationSequence.current;
    setConfigurationLoading(true); setConfigurationError(null);
    try {
      const value = await api<ModuleConfiguration>(`${path}/module-config`);
      if (!alive.current || request !== configurationSequence.current) return;
      setConfiguration(value);
      setConfigurationRemoteRevision(item.remote_revision);
      if (discard) {
        setForm(profileForm(value.profile, item.title)); setConfigurationRevision(value.revision);
        setConfigurationDirty(false); setConfigurationConflict(false); setTest(null);
      }
    } catch (failure) {
      if (alive.current && request === configurationSequence.current) setConfigurationError(failure);
    } finally {
      if (alive.current && request === configurationSequence.current) setConfigurationLoading(false);
    }
  }, [path, item.title, item.remote_revision]);
  useEffect(() => { if (configurationOpen && !configuration && item.remote_enabled) void readConfiguration(); }, [configurationOpen, configuration, readConfiguration, item.remote_enabled]);
  const savedProfile = profiles.find(profile => profile.id === item.platform_profile_id);
  const chosenProfile = profiles.find(profile => profile.id === profileId);
  const anyDirty = bindingDirty || configurationDirty;
  const pending = !!busy || configurationLoading || !item.remote_enabled;
  const configurationNeedsRefresh = !!configuration && configurationRemoteRevision !== item.remote_revision;
  const chooseSource = (value: AIServiceSource) => {
    setSource(value); setBindingDirty(true); setTest(null); setNotice('');
    if (value === 'module') setConfigurationOpen(true);
  };
  function edit<K extends keyof ProfileForm>(key: K, value: ProfileForm[K]) {
    setForm(previous => ({...previous, [key]: value})); setConfigurationDirty(true); setTest(null); setNotice('');
  }
  async function saveBinding() {
    setBusy('binding'); setError(null); setNotice(''); setTest(null);
    try {
      const value = await api<ModuleAIService>(`${path}/binding`, {method: 'PUT', body: JSON.stringify({expected_revision: bindingRevision, source, platform_profile_id: source === 'platform' ? profileId : null})});
      if (!alive.current) return;
      onSaved(value);
      setBindingDirty(false); setBindingConflict(false);
      if (alive.current) setNotice('AI 来源已保存。后续调用使用该来源；不会自动切换到另一来源。');
    } catch (failure) {
      if (!alive.current) return;
      setError(failure);
      if (revisionConflict(failure)) { setBindingConflict(true); await refresh(); }
    } finally { if (alive.current) setBusy(''); }
  }
  async function saveConfiguration() {
    if (configurationRevision === null) return;
    setBusy('configuration'); setError(null); setNotice(''); setTest(null);
    try {
      const {api_key, clear_api_key, ...profile} = form;
      const value = await api<ModuleConfiguration>(`${path}/module-config`, {method: 'PUT', body: JSON.stringify({expected_revision: configurationRevision, profile: {...profile, ...(api_key.trim() ? {api_key} : {}), clear_api_key}})});
      if (!alive.current) return;
      setConfiguration(value); setConfigurationRevision(value.revision); setForm(profileForm(value.profile, item.title));
      setConfigurationRemoteRevision(item.remote_revision);
      setConfigurationDirty(false); setConfigurationConflict(false);
      setNotice('模块 API 配置已保存。切换调用来源时，还需点击“保存 AI 来源”。');
    } catch (failure) {
      if (!alive.current) return;
      setError(failure);
      if (revisionConflict(failure)) { setConfigurationConflict(true); await readConfiguration(false); }
    } finally {
      if (alive.current) { setForm(previous => ({...previous, api_key: ''})); setBusy(''); }
    }
  }
  async function testConnection() {
    setBusy('test'); setError(null); setNotice(''); setTest(null);
    try {
      const value = await post<ServiceTest>(`${path}/test`, {expected_revision: item.revision});
      if (alive.current) setTest(value);
    } catch (failure) {
      if (!alive.current) return;
      setError(failure);
      if (revisionConflict(failure)) { setBindingConflict(true); await refresh(); }
    } finally { if (alive.current) setBusy(''); }
  }
  return <section className="module-ai-card" aria-label={`${item.title} · 模块 AI 服务`}>
    <div className="module-ai-title"><Server size={17}/><div><h3>{item.title}</h3><small>{item.module_id} · {item.id}</small></div></div>
    <p className="subtle">{item.description}</p>
    {!!item.capability_ids.length && <p className="subtle">用于：{item.capability_ids.join('、')}</p>}
    <div className="module-ai-current" aria-label="已保存的 AI 来源"><strong>已保存来源：{sourceName(item.source)}</strong><span>来源修订 {item.revision}</span>{item.source === 'platform' && <p>{savedProfile ? `${savedProfile.name} · ${savedProfile.model}${savedProfile.enabled ? '' : '（连接已停用）'}` : '原平台连接不存在，请重新选择。'}</p>}{item.source === 'module' && <p>密钥与连接参数保存在模块服务端。</p>}</div>
    {!item.remote_enabled && <div className="notice warning" role="status">模块已停用，当前 AI 来源不可使用。请先在服务接入中启用模块，再配置或测试。</div>}
    {item.binding_stale && <div className="notice warning" role="status">模块连接或声明已变化，已保存来源暂不可用。请核对后重新保存 AI 来源。</div>}
    <form onSubmit={event => { event.preventDefault(); void saveBinding(); }}>
      <div className="editor-title"><h4>选择调用来源</h4>{bindingDirty && <span className="tag amber">来源修改未保存</span>}</div>
      <div className="form-grid"><label className="field">AI 调用来源<select value={source} onChange={event => chooseSource(event.target.value as AIServiceSource)} disabled={pending}><option value="disabled">未启用 AI 服务</option>{item.supported_sources.includes('platform') && <option value="platform">主框架 API</option>}{item.supported_sources.includes('module') && <option value="module">模块自身 API</option>}</select></label>{source === 'platform' && <label className="field">主框架模型连接<select aria-label="主框架模型连接" value={profileId} onChange={event => { setProfileId(event.target.value); setBindingDirty(true); setNotice(''); setTest(null); }} disabled={pending} required><option value="">请选择一个已启用连接</option>{profiles.map(profile => <option key={profile.id} value={profile.id} disabled={!profile.enabled}>{profile.name} · {profile.model}{profile.enabled ? '' : '（已停用）'}</option>)}</select><small>不会自动使用默认模型。平台连接在上方维护。</small></label>}</div>
      {source === 'module' && <p className="subtle">先在下方保存模块 API 配置，再保存调用来源。</p>}
      {(bindingConflict || (bindingDirty && bindingRevision !== item.revision)) && <div className="notice warning" role="status">正在编辑来源修订 {bindingRevision}，服务器当前为修订 {item.revision}。草稿已保留，请载入最新来源后重新修改。</div>}
      <div className="form-actions"><button type="submit" className="button primary" disabled={pending || bindingConflict || bindingRevision !== item.revision || (source === 'platform' && (!chosenProfile || !chosenProfile.enabled)) || (source === 'module' && (configurationDirty || configurationNeedsRefresh || !configuration?.profile?.enabled))}><Save size={15}/>{busy === 'binding' ? '正在保存来源…' : '保存 AI 来源'}</button>{(bindingDirty || bindingConflict) && <button type="button" className="button secondary" disabled={pending} onClick={() => { setSource(item.source); setProfileId(item.platform_profile_id || ''); setBindingRevision(item.revision); setBindingDirty(false); setBindingConflict(false); setError(null); setTest(null); }}>载入最新来源并放弃修改</button>}</div>
    </form>
    {item.supported_sources.includes('module') && <details className="advanced module-ai-configuration" open={configurationOpen} onToggle={event => setConfigurationOpen(event.currentTarget.open)}><summary>模块自身 API 配置</summary><div className="module-ai-configuration-body">
      <ErrorNotice error={configurationError} onRetry={() => void readConfiguration()}/>
      {configurationNeedsRefresh && <div className="notice warning" role="status">模块连接已变化，下方配置来自旧连接。请重新读取当前模块配置后再保存。</div>}
      {configurationLoading ? <Loading label="正在读取模块 API 配置…"/> : configuration && <form onSubmit={event => { event.preventDefault(); void saveConfiguration(); }}>
        <div className="editor-title"><h4>模块服务端连接</h4><span className="tag">配置修订 {configuration.revision}</span>{configurationDirty && <span className="tag amber">连接修改未保存</span>}</div>
        <p className="subtle">{configuration.profile?.api_key_configured ? '模块已保存密钥，原值不回显。' : '模块尚未配置密钥。'} 留空保留已有密钥。</p>
        <div className="form-grid"><label className="field">模块连接名称<input required maxLength={120} value={form.name} disabled={pending} onChange={event => edit('name', event.target.value)}/></label><label className="field">模块模型标识<input required maxLength={200} value={form.model} disabled={pending} placeholder="填写服务商提供的模型名称" onChange={event => edit('model', event.target.value)}/></label><label className="field wide">模块 API 基础地址<input aria-label="模块 API 基础地址" type="url" required maxLength={2048} value={form.base_url} disabled={pending} placeholder="https://example.com/v1" onChange={event => edit('base_url', event.target.value)}/><small>填写到基础路径，模块自动添加 /chat/completions。更换地址时需输入新密钥，或明确移除已有密钥。</small></label><label className="field wide">模块 API Key<input aria-label="模块 API Key" type="password" autoComplete="new-password" maxLength={8192} value={form.api_key} disabled={pending || form.clear_api_key} placeholder={configuration.profile?.api_key_configured ? '留空保留已有密钥' : '输入模块使用的 API Key'} onChange={event => edit('api_key', event.target.value)}/><small>经主框架转交模块保存，不进入对话或浏览器持久存储。请求结束后清空此输入。</small></label></div>
        {configuration.profile?.api_key_configured && <label className="checkbox-row"><input type="checkbox" checked={form.clear_api_key} disabled={pending} onChange={event => { edit('clear_api_key', event.target.checked); if (event.target.checked) setForm(previous => ({...previous, api_key: ''})); }}/>保存时移除模块已有密钥</label>}
        <label className="checkbox-row"><input type="checkbox" checked={form.enabled} disabled={pending} onChange={event => edit('enabled', event.target.checked)}/>启用模块连接</label>
        <div className="form-grid three"><label className="field">模块 Temperature<input type="number" min="0" max="2" step="any" value={form.temperature} disabled={pending} onChange={event => edit('temperature', Number(event.target.value))}/></label><label className="field">模块最大输出 Token<input type="number" min="1" max="131072" value={form.max_tokens} disabled={pending} onChange={event => edit('max_tokens', Number(event.target.value))}/></label><label className="field">模块超时（秒）<input type="number" min="1" max="300" step="any" value={form.timeout_seconds} disabled={pending} onChange={event => edit('timeout_seconds', Number(event.target.value))}/></label></div>
        {configurationConflict && <div className="notice warning" role="status">模块配置已变化，草稿已保留。请重新读取配置后再修改；密钥需重新输入。</div>}
        <div className="form-actions"><button type="submit" className="button secondary" disabled={pending || configurationNeedsRefresh || configurationConflict}><Save size={15}/>{busy === 'configuration' ? '正在保存模块配置…' : '保存模块 API 配置'}</button><button type="button" className="text-button" disabled={pending} onClick={() => void readConfiguration()}>{configurationDirty ? '放弃模块修改并重新读取' : '重新读取模块配置'}</button></div>
      </form>}
    </div></details>}
    <ErrorNotice error={error}/>{notice && <Success>{notice}</Success>}
    {test && !item.binding_stale && item.remote_enabled && test.binding_revision === item.revision && test.source === item.source && <div className={`test-result ${test.result.ok ? 'passed' : 'not-passed'}`} role="status"><div className="test-result-heading">{test.result.ok ? <Check size={17}/> : <CircleDot size={17}/>}<strong>{test.result.ok ? 'AI 连接测试成功' : 'AI 连接测试未通过'}</strong><span>{test.result.latency_ms} ms</span></div><p>{test.result.message}</p>{test.result.response_preview && <blockquote>{test.result.response_preview}</blockquote>}<small>{sourceName(test.source)} · 来源修订 {test.binding_revision}{test.module_config_revision !== null && test.module_config_revision !== undefined && <> · 模块配置修订 {test.module_config_revision}</>} · {test.result.model}。测试使用合成文本，不代表业务已完成。</small></div>}
    <div className="form-actions"><button type="button" className="button secondary" disabled={pending || anyDirty || bindingConflict || item.binding_stale || item.source === 'disabled'} onClick={() => void testConnection()}><FlaskConical size={15}/>{busy === 'test' ? '正在测试已保存来源…' : '测试已保存的 AI 来源'}</button>{anyDirty && <small className="subtle">请先保存或放弃修改，再测试已保存来源。</small>}</div>
  </section>;
}
