import { useCallback, useEffect, useRef, useState } from 'react';
import { Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { api, ApiError, post } from './api';
import { Empty, ErrorNotice, Loading, Success } from './ui';

type AccessUser = {id:string; username:string; name:string; enabled:boolean};
type RoleTemplate = {id:string; title:string; permissions:string[]; required_scope:string[]};
type Definition = {
  module_id:string; title:string; version:string; enabled:boolean; policy_revision:number;
  scope_keys:string[]; role_templates:RoleTemplate[]; permissions:{id:string;title:string}[];
};
type Grant = {
  id:string; subject_id:string; module_id:string; role_id:string; scope:Record<string,string>;
  permissions:string[]; required_scope:string[]; definition_revision:number;
  expires_at:number|null; revoked:boolean; created_at:string;
};

const scopeTitles:Record<string,string> = {project_id:'项目',organization_id:'机构',workspace_id:'工作空间',team_id:'团队',owner_id:'对象所有者'};

export default function AccessManagement({onChanged}:{onChanged?:()=>void}) {
  const [definitions,setDefinitions] = useState<Definition[]>([]);
  const [users,setUsers] = useState<AccessUser[]>([]);
  const [subjectId,setSubjectId] = useState('');
  const [moduleId,setModuleId] = useState('');
  const [roleId,setRoleId] = useState('');
  const [scope,setScope] = useState<Record<string,string>>({});
  const [expires,setExpires] = useState('');
  const [grants,setGrants] = useState<Grant[]>([]);
  const [catalogLoading,setCatalogLoading] = useState(true);
  const [grantsLoading,setGrantsLoading] = useState(false);
  const [catalogError,setCatalogError] = useState<unknown>();
  const [grantsError,setGrantsError] = useState<unknown>();
  const [error,setError] = useState<unknown>();
  const [notice,setNotice] = useState('');
  const [busy,setBusy] = useState('');
  const alive = useRef(true);
  const catalogSequence = useRef(0);
  const grantsSequence = useRef(0);
  const subject = useRef('');
  const writing = useRef(false);

  const loadCatalog = useCallback(async()=>{
    const sequence = ++catalogSequence.current;
    setCatalogLoading(true);setCatalogError(null);
    try {
      const [policies,identities] = await Promise.all([
        api<{items:Definition[]}>('/access/definitions'), api<{items:AccessUser[]}>('/identity/users'),
      ]);
      if(!alive.current||sequence!==catalogSequence.current)return;
      setDefinitions(policies.items);setUsers(identities.items);
      setModuleId(current=>policies.items.some(item=>item.module_id===current)?current:policies.items.find(item=>item.enabled)?.module_id||'');
    } catch(e) {if(alive.current&&sequence===catalogSequence.current)setCatalogError(e);}
    finally {if(alive.current&&sequence===catalogSequence.current)setCatalogLoading(false);}
  },[]);

  const loadGrants = useCallback(async(userId:string)=>{
    const sequence = ++grantsSequence.current;
    setGrantsError(null);
    if(!userId){setGrants([]);setGrantsLoading(false);return;}
    setGrantsLoading(true);
    try {
      const result = await api<{items:Grant[]}>(`/access/grants?subject_id=${encodeURIComponent(userId)}`);
      if(alive.current&&sequence===grantsSequence.current&&subject.current===userId){
        setGrants(result.items.filter(grant=>grant.subject_id===userId));
      }
    } catch(e) {if(alive.current&&sequence===grantsSequence.current&&subject.current===userId){setGrants([]);setGrantsError(e);}}
    finally {if(alive.current&&sequence===grantsSequence.current&&subject.current===userId)setGrantsLoading(false);}
  },[]);

  useEffect(()=>{
    alive.current=true;void loadCatalog();
    return()=>{alive.current=false;catalogSequence.current++;grantsSequence.current++;};
  },[loadCatalog]);
  useEffect(()=>{void loadGrants(subjectId);},[subjectId,loadGrants]);

  const definition = definitions.find(item=>item.module_id===moduleId);
  const role = definition?.role_templates.find(item=>item.id===roleId)||definition?.role_templates[0];
  const user = users.find(item=>item.id===subjectId);

  function chooseSubject(id:string){
    if(writing.current)return;
    subject.current=id;grantsSequence.current++;setSubjectId(id);setGrants([]);setGrantsError(null);setError(null);setNotice('');
    setGrantsLoading(!!id);
  }
  function chooseModule(id:string){setModuleId(id);setRoleId('');setScope({});setError(null);setNotice('');}
  function chooseRole(id:string){setRoleId(id);setScope({});setError(null);setNotice('');}

  async function grant(){
    if(writing.current||catalogLoading||catalogError||!user?.enabled||!definition?.enabled||!role)return;
    const values = Object.fromEntries(definition.scope_keys.map(key=>[key,(scope[key]||'').trim()]).filter(([,value])=>value));
    if(role.required_scope.some(key=>!values[key])||Object.values(values).some(value=>value==='*')){
      setError(new Error('请填写全部必需范围；范围应为明确编号，不能使用 *。'));return;
    }
    const expiry = expires?new Date(expires).getTime()/1000:null;
    if(expiry!==null&&(!Number.isFinite(expiry)||expiry<=Date.now()/1000)){
      setError(new Error('授权到期时间必须晚于当前时间。'));return;
    }
    writing.current=true;setBusy('grant');setError(null);setNotice('');
    const currentSubject=subjectId;
    try {
      await post('/access/grants',{
        subject_id:currentSubject,module_id:definition.module_id,role_id:role.id,scope:values,
        ...(expiry===null?{}:{expires_at:expiry}),expected_policy_revision:definition.policy_revision,
      });
      if(!alive.current||subject.current!==currentSubject)return;
      setNotice('授权已保存。该账号可在相应业务范围内打开页面与对话入口。');onChanged?.();
      await Promise.all([loadCatalog(),loadGrants(currentSubject)]);
    } catch(e) {if(alive.current&&subject.current===currentSubject)setError(e);}
    finally {writing.current=false;if(alive.current)setBusy('');}
  }

  async function revoke(grantId:string){
    if(writing.current)return;
    writing.current=true;setBusy(grantId);setError(null);setNotice('');
    const currentSubject=subjectId;
    try {
      await post(`/access/grants/${encodeURIComponent(grantId)}/revoke`);
      if(!alive.current||subject.current!==currentSubject)return;
      setNotice('授权已撤销。旧的受保护流程和结果将在继续访问时重新校验。');onChanged?.();
      await Promise.all([loadCatalog(),loadGrants(currentSubject)]);
    } catch(e) {if(alive.current&&subject.current===currentSubject)setError(e);}
    finally {writing.current=false;if(alive.current)setBusy('');}
  }

  function status(grant:Grant){
    if(grant.revoked)return '已撤销';
    if(grant.expires_at!==null&&grant.expires_at<=Date.now()/1000)return '已到期';
    const policy=definitions.find(item=>item.module_id===grant.module_id);
    if(!policy?.enabled)return '模块授权已停用';
    const template=policy.role_templates.find(item=>item.id===grant.role_id);
    if(!template||template.required_scope.some(key=>!grant.scope[key])||!template.permissions.some(permission=>grant.permissions.includes(permission)))return '定义已变更，需重授';
    return '有效';
  }
  const conflict=error instanceof ApiError&&error.code==='access_policy_conflict';

  return <section className="model-settings" aria-label="模块业务授权">
    <div className="model-intro"><div className="icon-tile"><ShieldCheck size={22}/></div><div><h2>按模块和业务范围分配职责</h2><p>账号保持统一；每条授权明确一个模块、一个角色及一组同时满足的范围。</p></div><button className="button secondary" disabled={!!busy||catalogLoading} onClick={()=>{void loadCatalog();void loadGrants(subject.current);}}><RefreshCw size={15}/>刷新授权目录</button></div>
    <div className="model-editor">
      <ErrorNotice error={catalogError} onRetry={()=>void loadCatalog()}/>
      {catalogLoading&&definitions.length===0?<Loading label="正在读取模块权限定义…"/>:<>
        <label className="field">授权账号<select value={subjectId} disabled={!!busy} onChange={event=>chooseSubject(event.target.value)}><option value="">选择需要查看或授权的账号</option>{users.map(item=><option key={item.id} value={item.id}>{item.name}（{item.username}）{item.enabled?'':' · 已停用'}</option>)}</select></label>
        {!definitions.length?<Empty title="尚未注册模块权限定义" description="模块安装权限定义后，角色模板和范围字段会出现在这里。"/>:<form aria-label="添加模块授权" onSubmit={event=>{event.preventDefault();void grant();}}>
          <fieldset disabled={!!busy||catalogLoading||!!catalogError} style={{border:0,padding:0,margin:0,minWidth:0}}>
            <div className="form-grid">
              <label className="field">业务模块<select value={moduleId} onChange={event=>chooseModule(event.target.value)}>{definitions.map(item=><option key={item.module_id} value={item.module_id}>{item.title}{item.enabled?'':' · 已停用'}</option>)}</select></label>
              <label className="field">业务角色<select value={role?.id||''} onChange={event=>chooseRole(event.target.value)}>{definition?.role_templates.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
            </div>
            {role&&definition&&<>
              <p className="subtle">角色提供：{role.permissions.map(permission=>definition.permissions.find(item=>item.id===permission)?.title||permission).join('、')}。策略修订 {definition.policy_revision}。</p>
              <div className="form-grid">{definition.scope_keys.map(key=><label className="field" key={key}>{scopeTitles[key]||'范围'}（{key}）{role.required_scope.includes(key)?' · 必填':' · 可选'}<input aria-label={`范围 ${key}`} required={role.required_scope.includes(key)} maxLength={160} value={scope[key]||''} placeholder="填写明确的业务编号" onChange={event=>setScope(current=>({...current,[key]:event.target.value}))}/></label>)}</div>
              <p className="subtle">同一条授权内的范围条件同时生效。不同项目的角色不会相互拼接。</p>
            </>}
            <label className="field">到期时间（可选）<input type="datetime-local" value={expires} onChange={event=>setExpires(event.target.value)}/><small>按当前设备本地时间填写；留空表示不设置自动到期时间。</small></label>
            <div className="form-actions"><button type="submit" className="button primary" disabled={!user?.enabled||!definition?.enabled||!role}>{busy==='grant'?<Loading label="保存授权中…"/>:<><Plus size={16}/>添加授权</>}</button>{user&&!user.enabled&&<span className="subtle">账号已停用，仍可查看和撤销历史授权。</span>}</div>
          </fieldset>
        </form>}
      </>}
      <ErrorNotice error={error}/>
      {conflict&&<div className="notice warning"><span>策略已被其他操作更新。表单内容已保留，请刷新授权目录，再核对并提交。</span></div>}
      {notice&&<Success>{notice}</Success>}
      <div className="subsection-heading" style={{marginTop:28}}><h3>{user?`${user.name}的模块授权`:'账号的模块授权'}</h3><span>角色模板和范围共同生效</span></div>
      <ErrorNotice error={grantsError} onRetry={()=>void loadGrants(subject.current)}/>
      {!subjectId?<Empty title="先选择一个账号" description="可以分别给同一账号分配多个模块、多个项目的职责。"/>:grantsLoading?<Loading label="正在读取该账号授权…"/>:!grants.length?<Empty title="此账号暂无模块授权"/>:<div className="table-wrap"><table><thead><tr><th>模块与角色</th><th>授权范围</th><th>状态</th><th>到期时间</th><th>操作</th></tr></thead><tbody>{[...grants].reverse().map(item=>{
        const policy=definitions.find(entry=>entry.module_id===item.module_id);const template=policy?.role_templates.find(entry=>entry.id===item.role_id);const state=status(item);
        return <tr key={item.id}><td><strong>{policy?.title||item.module_id}</strong><div>{template?.title||item.role_id}</div></td><td>{Object.entries(item.scope).map(([key,value])=><div key={key}><code>{key}</code>：{value}</div>)}</td><td><span className={'tag '+(state==='有效'?'green':'amber')}>{state}</span></td><td>{item.expires_at===null?'不自动到期':new Date(item.expires_at*1000).toLocaleString('zh-CN')}</td><td>{!item.revoked&&<button type="button" className="button secondary small" disabled={!!busy} aria-label={`撤销授权 ${item.id}`} onClick={()=>void revoke(item.id)}>{busy===item.id?'正在撤销…':'撤销授权'}</button>}</td></tr>;
      })}</tbody></table></div>}
    </div>
  </section>;
}
