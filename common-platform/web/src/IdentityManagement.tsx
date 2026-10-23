import { useCallback, useEffect, useRef, useState } from 'react';
import { Building2, Check, KeyRound, Plus, RefreshCw, Save, ShieldCheck, Users, X } from 'lucide-react';
import { api, ApiError, post } from './api';
import { Empty, ErrorNotice, Loading, Success } from './ui';

type IdentityUser = {id:string;username:string;name:string;role:string;roles:string[];organization_id:string|null;team_ids:string[];authorization_revision:number;enabled:boolean};
type Organization = {id:string;name:string;kind:string;enabled:boolean};
type Team = {id:string;name:string;organization_id:string;enabled:boolean};
type Role = {id:string;name:string;implies:string[];permissions:string[]};
type Catalog = {users:IdentityUser[];organizations:Organization[];teams:Team[];roles:Role[]};
type UserForm = {username:string;name:string;role:string;roles:string[];organization_id:string;team_ids:string[];password:string;revision:number};
type Secret = {username:string;password:string};
type Tab = 'users'|'organizations'|'teams';
const fresh = ():UserForm => ({username:'',name:'',role:'member',roles:['member'],organization_id:'',team_ids:[],password:'',revision:1});
const toForm = (user:IdentityUser):UserForm => ({username:user.username,name:user.name,role:user.role,roles:[...user.roles],organization_id:user.organization_id||'',team_ids:[...user.team_ids],password:'',revision:user.authorization_revision});
const kinds:Record<string,string> = {research:'研究型机构（兼容分类）',contributor:'协作机构（兼容分类）',museum:'管理机构（兼容分类）',other:'通用机构'};

export default function IdentityManagement({onChanged}:{onChanged?:()=>void}) {
  const [catalog,setCatalog] = useState<Catalog>({users:[],organizations:[],teams:[],roles:[]});
  const [tab,setTab] = useState<Tab>('users');
  const [selected,setSelected] = useState<string|null>(null);
  const [form,setForm] = useState<UserForm>(fresh);
  const [dirty,setDirty] = useState(false);
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState('');
  const [error,setError] = useState<unknown>();
  const [notice,setNotice] = useState('');
  const [secret,setSecret] = useState<Secret|null>(null);
  const [confirmAction,setConfirmAction] = useState<'disable'|'reset'|null>(null);
  const [pendingSelection,setPendingSelection] = useState<{user:IdentityUser|null}|null>(null);
  const [filter,setFilter] = useState('');
  const [organization,setOrganization] = useState({name:'',kind:'other'});
  const [team,setTeam] = useState({name:'',organization_id:''});
  const sequence = useRef(0);

  const load = useCallback(async()=>{
    const request = ++sequence.current;
    setLoading(true);
    try {
      const [users,organizations,teams,roles] = await Promise.all([
        api<{items:IdentityUser[]}>('/identity/users'),
        api<{items:Organization[]}>('/identity/organizations'),
        api<{items:Team[]}>('/identity/teams'),
        api<{roles:Role[]}>('/identity/roles'),
      ]);
      const next = {users:users.items,organizations:organizations.items,teams:teams.items,roles:roles.roles};
      if(request===sequence.current)setCatalog(next);
      return next;
    } catch(e) {if(request===sequence.current)setError(e);return null;}
    finally {if(request===sequence.current)setLoading(false);}
  },[]);
  useEffect(()=>{void load();return()=>{sequence.current++;};},[load]);

  const selectUser = (user:IdentityUser|null) => {
    setSelected(user?.id||null);setForm(user?toForm(user):fresh());setDirty(false);
    setSecret(null);setError(null);setNotice('');setConfirmAction(null);setPendingSelection(null);
  };
  const requestSelection = (user:IdentityUser|null) => {
    if(dirty){setPendingSelection({user});return;}
    selectUser(user);
  };
  function update<K extends keyof UserForm>(key:K,value:UserForm[K]) {
    setForm(current=>({...current,[key]:value}));setDirty(true);setNotice('');setConfirmAction(null);setSecret(null);
  }
  function updatePrimary(role:string) {
    setForm(current=>({...current,role,roles:current.roles.includes(role)?current.roles:[...current.roles,role]}));
    setDirty(true);setSecret(null);setNotice('');setConfirmAction(null);
  }
  function updateOrganization(organization_id:string) {
    setForm(current=>({...current,organization_id,team_ids:[]}));setDirty(true);setSecret(null);setNotice('');setConfirmAction(null);
  }
  function accept(user:IdentityUser) {
    setCatalog(current=>({...current,users:current.users.some(item=>item.id===user.id)?current.users.map(item=>item.id===user.id?user:item):[...current.users,user]}));
    selectUser(user);
  }
  async function refresh() {
    setSecret(null);setError(null);
    const next = await load();
    if(next&&!dirty&&selected){const item=next.users.find(user=>user.id===selected);if(item)selectUser(item);}
  }
  async function saveUser() {
    setBusy('save');setError(null);setNotice('');setSecret(null);
    const body={name:form.name,role:form.role,roles:form.roles,organization_id:form.organization_id||null,team_ids:form.team_ids};
    try {
      if(selected){
        const result=await api<IdentityUser>(`/identity/users/${encodeURIComponent(selected)}`,{method:'PATCH',body:JSON.stringify({...body,expected_revision:form.revision})});
        accept(result);setNotice('账号已保存。角色和归属变化会使该账号的原登录会话失效。');
      }else{
        const result=await post<{user:IdentityUser;initial_password:string}>('/identity/users',{...body,username:form.username,...(form.password?{password:form.password}:{})});
        accept(result.user);setSecret({username:result.user.username,password:result.initial_password});setNotice('账号已创建，请保存本次显示的初始密码。');
      }
      onChanged?.();
    }catch(e){setError(e);}finally{setBusy('');}
  }
  async function changeEnabled(enable:boolean) {
    if(!selected)return;
    setBusy('status');setError(null);setNotice('');setSecret(null);
    try {
      const result=await post<IdentityUser>(`/identity/users/${encodeURIComponent(selected)}/${enable?'enable':'disable'}`,{expected_revision:form.revision});
      accept(result);setNotice(enable?'账号已启用，可以重新登录。':'账号已停用，全部原登录会话已撤销。');onChanged?.();
    }catch(e){setError(e);}finally{setBusy('');}
  }
  async function resetPassword() {
    if(!selected)return;
    setBusy('reset');setError(null);setNotice('');setSecret(null);
    try {
      const result=await post<{user:IdentityUser;initial_password:string}>(`/identity/users/${encodeURIComponent(selected)}/reset-password`,{expected_revision:form.revision});
      accept(result.user);setSecret({username:result.user.username,password:result.initial_password});setNotice('密码已重置，原登录会话已撤销。请保存本次显示的新密码。');onChanged?.();
    }catch(e){setError(e);}finally{setBusy('');}
  }
  async function createOrganization() {
    setBusy('organization');setError(null);setNotice('');setSecret(null);
    try {
      const result=await post<Organization>('/identity/organizations',organization);
      setCatalog(current=>({...current,organizations:[...current.organizations,result]}));setOrganization({name:'',kind:'other'});
      setNotice(`机构“${result.name}”已创建，可以继续建立团队并分配账号。`);onChanged?.();
    }catch(e){setError(e);}finally{setBusy('');}
  }
  async function createTeam() {
    setBusy('team');setError(null);setNotice('');setSecret(null);
    try {
      const result=await post<Team>('/identity/teams',team);
      setCatalog(current=>({...current,teams:[...current.teams,result]}));setTeam(current=>({...current,name:''}));
      setNotice(`团队“${result.name}”已创建，可以为同机构账号分配此团队。`);onChanged?.();
    }catch(e){setError(e);}finally{setBusy('');}
  }
  const saved=catalog.users.find(user=>user.id===selected);
  const orgName=(id:string|null)=>catalog.organizations.find(item=>item.id===id)?.name||'未分配机构';
  const roleName=(id:string)=>catalog.roles.find(item=>item.id===id)?.name||id;
  const availableRoles=catalog.roles.filter(role=>role.id!=='admin'||saved?.roles.includes('admin'));
  const activeOrganizations=catalog.organizations.filter(item=>item.enabled);
  const matchingTeams=catalog.teams.filter(item=>item.enabled&&item.organization_id===form.organization_id);
  const matchingUsers=catalog.users.filter(user=>`${user.name} ${user.username} ${orgName(user.organization_id)}`.toLowerCase().includes(filter.toLowerCase()));
  const impliedRoles=new Set(form.roles.flatMap(role=>catalog.roles.find(item=>item.id===role)?.implies||[]));
  const revisionConflict=error instanceof ApiError&&error.code==='revision_conflict';

  return <section className="model-settings" aria-label="统一身份管理">
    <div className="model-intro"><div className="icon-tile"><ShieldCheck size={22}/></div><div><h2>管理身份、机构与团队</h2><p>系统管理员管理平台账号；业务访问仍按明确分配的角色和对象范围判断。</p></div><span className="tag">{catalog.users.length} 个账号</span></div>
    <div className="form-actions" style={{padding:'0 24px 18px',borderBottom:'1px solid var(--border)'}} role="tablist" aria-label="身份管理分类">
      {([{id:'users',label:'账号与角色',icon:<Users size={15}/>},{id:'organizations',label:'机构',icon:<Building2 size={15}/>},{id:'teams',label:'团队',icon:<Users size={15}/>} ] as const).map(item=><button key={item.id} role="tab" aria-selected={tab===item.id} className={'button '+(tab===item.id?'primary':'secondary')} onClick={()=>{setTab(item.id);setSecret(null);setError(null);setNotice('');setConfirmAction(null);}} disabled={!!busy}>{item.icon}{item.label}</button>)}
      <button className="text-button push-right" onClick={()=>void refresh()} disabled={!!busy||loading}><RefreshCw size={14} className={loading?'spin':''}/>刷新目录</button>
    </div>
    {loading&&catalog.roles.length===0?<div style={{padding:24}}><Loading label="正在读取身份目录…"/></div>:tab==='users'?<div className="model-layout">
      <aside className="model-list">
        <button className="button secondary full" disabled={!!busy} onClick={()=>requestSelection(null)}><Plus size={16}/>创建账号</button>
        <label className="field">查找账号<input value={filter} placeholder="姓名、账号或机构" onChange={event=>setFilter(event.target.value)}/></label>
        <div style={{maxHeight:'65dvh',overflowY:'auto'}}>{matchingUsers.map(user=><button key={user.id} className={'model-item '+(selected===user.id?'selected':'')} disabled={!!busy} onClick={()=>requestSelection(user)}><span className="model-item-name"><Users size={15}/>{user.name}</span><small>{user.username} · {roleName(user.role)}</small><span className={'connection-dot '+(user.enabled?'enabled':'')}>{user.enabled?'已启用':'已停用'} · {orgName(user.organization_id)}</span></button>)}{!matchingUsers.length&&<p className="subtle">暂无匹配账号。</p>}</div>
      </aside>
      <div className="model-editor">
        <div className="editor-title"><h3>{selected?'编辑账号':'创建平台账号'}</h3>{dirty&&<span className="tag amber">有未保存修改</span>}{saved&&<span className={'tag '+(saved.enabled?'green':'amber')}>{saved.enabled?'已启用':'已停用'} · 修订 {form.revision}</span>}</div>
        {pendingSelection&&<div className="notice warning"><div>当前账号有未保存修改。切换将丢弃这些修改。</div><button className="text-button" onClick={()=>selectUser(pendingSelection.user)}>丢弃并切换</button><button className="text-button" onClick={()=>setPendingSelection(null)}>继续编辑</button></div>}
        <form onSubmit={event=>{event.preventDefault();void saveUser();}}>
          <div className="form-grid">
            <label className="field">账号名<input required minLength={3} maxLength={80} pattern="[A-Za-z0-9][A-Za-z0-9_.@-]{2,79}" value={form.username} disabled={!!selected||!!busy} autoComplete="off" onChange={event=>update('username',event.target.value)}/><small>用于登录。创建后保留账号名以关联历史记录。</small></label>
            <label className="field">姓名或显示名<input required maxLength={100} value={form.name} disabled={!!busy} onChange={event=>update('name',event.target.value)}/></label>
            <label className="field">主角色<select required value={form.role} disabled={!!busy} onChange={event=>updatePrimary(event.target.value)}>{availableRoles.map(role=><option key={role.id} value={role.id}>{role.name}</option>)}</select><small>主角色包含在下方角色集合中，用于身份显示。</small></label>
            <label className="field">所属机构<select value={form.organization_id} disabled={!!busy} onChange={event=>updateOrganization(event.target.value)}><option value="">未分配机构</option>{activeOrganizations.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select><small>变更机构会清空当前团队，请重新选择。</small></label>
          </div>
          <fieldset style={{border:0,padding:0,margin:'0 0 20px'}} disabled={!!busy}><legend className="subtle" style={{marginBottom:10}}>分配角色（可多选）</legend><div className="toggle-row" style={{flexWrap:'wrap'}}>{availableRoles.map(role=><label key={role.id} className="checkbox-row"><input type="checkbox" checked={form.roles.includes(role.id)||impliedRoles.has(role.id)} disabled={role.id===form.role||impliedRoles.has(role.id)} onChange={event=>update('roles',event.target.checked?[...form.roles,role.id]:form.roles.filter(id=>id!==role.id))}/>{role.name}{impliedRoles.has(role.id)&&<span className="tag">角色继承</span>}</label>)}</div><p className="subtle">系统管理员不自动获得业务数据读取权限。旧演示管理员角色只能保留或移除。</p></fieldset>
          <fieldset style={{border:0,padding:0,margin:'0 0 20px'}} disabled={!!busy}><legend className="subtle" style={{marginBottom:10}}>所属团队</legend>{matchingTeams.length?<div className="toggle-row" style={{flexWrap:'wrap'}}>{matchingTeams.map(item=><label key={item.id} className="checkbox-row"><input type="checkbox" checked={form.team_ids.includes(item.id)} onChange={event=>update('team_ids',event.target.checked?[...form.team_ids,item.id]:form.team_ids.filter(id=>id!==item.id))}/>{item.name}</label>)}</div>:<p className="subtle">{form.organization_id?'该机构还没有团队，可在“团队”中创建。':'先选择所属机构，再分配团队。'}</p>}<p className="subtle">加入同一机构或团队不会自动共享私有资源和对话。</p></fieldset>
          {!selected&&<label className="field">初始密码（可选）<input type="password" minLength={12} maxLength={200} autoComplete="new-password" placeholder="留空由平台生成随机密码" value={form.password} disabled={!!busy} onChange={event=>update('password',event.target.value)}/><small>至少 12 个字符。密码只在创建成功后显示本次结果，不保存在浏览器持久存储。</small></label>}
          <div className="form-actions"><button type="submit" className="button primary" disabled={!!busy||loading||(!dirty&&!!selected)}>{busy==='save'?<Loading label="保存中"/>:<><Save size={16}/>{selected?'保存账号':'创建账号'}</>}</button>{selected&&<button type="button" className="button secondary" disabled={!!busy||!dirty} onClick={()=>{if(saved)selectUser(saved);}}>撤销未保存修改</button>}</div>
        </form>
        {saved&&<div className="service-help"><p className="subtle">角色、机构、团队、启用状态和密码变化会撤销该账号原有登录会话。最后一个可用平台管理员受到保护。</p><div className="form-actions"><button className={'button '+(saved.enabled?'danger':'secondary')} disabled={!!busy||dirty} onClick={()=>saved.enabled?setConfirmAction('disable'):void changeEnabled(true)}>{saved.enabled?'停用账号':'启用账号'}</button><button className="button secondary" disabled={!!busy||dirty} onClick={()=>setConfirmAction('reset')}><KeyRound size={15}/>重置密码</button></div></div>}
        {confirmAction&&saved&&<div className="notice warning"><div>{confirmAction==='reset'?`为“${saved.name}”生成新密码？原密码与登录会话将失效。`:`停用“${saved.name}”？该账号将无法登录，原登录会话会立即失效。`}</div><button className="button danger small" disabled={!!busy} onClick={()=>void (confirmAction==='reset'?resetPassword():changeEnabled(false))}>确认{confirmAction==='reset'?'重置':'停用'}</button><button className="text-button" disabled={!!busy} onClick={()=>setConfirmAction(null)}>取消</button></div>}
        {revisionConflict&&<div className="notice warning"><div>其他操作已改变该账号。请读取最新状态，再重新编辑。</div><button className="text-button" disabled={!!busy||loading} onClick={()=>{void load().then(next=>{const item=next?.users.find(user=>user.id===selected);if(item)selectUser(item);});}}>丢弃修改并读取最新</button></div>}
      </div>
    </div>:<div className="model-editor">
      {tab==='organizations'?<>
        <div className="editor-title"><h3>建立机构目录</h3><span className="tag">{catalog.organizations.length} 个机构</span></div>
        <form onSubmit={event=>{event.preventDefault();void createOrganization();}}><div className="form-grid"><label className="field">机构名称<input required maxLength={160} value={organization.name} disabled={!!busy} onChange={event=>setOrganization(current=>({...current,name:event.target.value}))}/></label><label className="field">机构类型<select value={organization.kind} disabled={!!busy} onChange={event=>setOrganization(current=>({...current,kind:event.target.value}))}>{Object.entries(kinds).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label></div><button className="button primary" type="submit" disabled={!!busy||loading}><Plus size={16}/>创建机构</button></form>
        <div className="artifact-list" style={{marginTop:24}}>{catalog.organizations.map(item=><div className="resource-card" key={item.id}><h3>{item.name}</h3><p className="subtle">{kinds[item.kind]||item.kind}</p><span className={'tag '+(item.enabled?'green':'amber')}>{item.enabled?'可用':'已停用'}</span></div>)}</div>
      </>:<>
        <div className="editor-title"><h3>建立机构内团队</h3><span className="tag">{catalog.teams.length} 个团队</span></div>
        <form onSubmit={event=>{event.preventDefault();void createTeam();}}><div className="form-grid"><label className="field">团队名称<input required maxLength={160} value={team.name} disabled={!!busy} onChange={event=>setTeam(current=>({...current,name:event.target.value}))}/></label><label className="field">所属机构<select required value={team.organization_id} disabled={!!busy} onChange={event=>setTeam(current=>({...current,organization_id:event.target.value}))}><option value="">请选择机构</option>{activeOrganizations.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div><button className="button primary" type="submit" disabled={!!busy||loading||!activeOrganizations.length}><Plus size={16}/>创建团队</button></form>
        {!activeOrganizations.length&&<Empty title="请先创建机构" description="团队归属于一个机构，账号只能加入所属机构内的团队。"/>}
        <div className="artifact-list" style={{marginTop:24}}>{catalog.teams.map(item=><div className="resource-card" key={item.id}><h3>{item.name}</h3><p className="subtle">{orgName(item.organization_id)}</p><span className={'tag '+(item.enabled?'green':'amber')}>{item.enabled?'可用':'已停用'}</span></div>)}</div>
      </>}
    </div>}
    <div style={{padding:'0 24px 20px'}}><ErrorNotice error={error}/>{notice&&<Success>{notice}</Success>}
      {secret&&<div className="notice info" role="status"><KeyRound size={19}/><div><strong>请保存本次密码</strong><p>账号：{secret.username}</p><code style={{overflowWrap:'anywhere',userSelect:'all'}}>{secret.password}</code><p>关闭、切换或刷新后不再显示；需要时可重新设置密码。请通过合适渠道交给本人。</p></div><button className="icon-button" aria-label="关闭初始密码" onClick={()=>setSecret(null)}><X size={17}/></button></div>}
      {!error&&catalog.roles.length>0&&<div className="subtle" style={{display:'flex',gap:7,alignItems:'center'}}><Check size={13}/>身份由平台集中维护；接入的微服务使用平台提供的身份上下文。</div>}
    </div>
  </section>;
}
