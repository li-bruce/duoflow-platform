// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import WorkspaceView, { AgentFlowView } from './WorkspaceView';
import CapabilityForm from './CapabilityForm';
import type { UIContext } from './ResultView';
import type { Capability, Data } from './types';
import { post } from './api';

vi.mock('./api',()=>({post:vi.fn(),api:vi.fn().mockResolvedValue({items:[]}),requestId:()=> 'synthetic-invocation',errorText:(e:Error)=>e.message}));
const capability:Capability={id:'synthetic.next',module_id:'synthetic',version:'1',title:'继续办理',description:'合成流程',kind:'command',roles:['member'],input_schema:{type:'object',properties:{task_id:{type:'string',title:'任务编号'},expected_revision:{type:'integer',title:'任务版本'}},required:['task_id'],additionalProperties:false},output_schema:{type:'object'},execution:{mode:'background',confirmation:true},presentation:{page:{renderer:'workspace'},conversation:{renderer:'workspace'}}};
function context():UIContext{return {user:{id:'user-a',username:'synthetic',name:'合成用户',role:'member'},modules:[],capabilities:[capability],conversationId:'conversation-a',onChanged:vi.fn(),onResult:vi.fn(),onFormInputChange:vi.fn()};}
function show(blocks:Data[],ctx=context()){render(<WorkspaceView data={{blocks}} context={ctx}/>);return ctx;}
afterEach(()=>{cleanup();vi.restoreAllMocks();});
beforeEach(()=>{vi.mocked(post).mockReset();});

describe('structured multimodal workspace',()=>{
  it('filters, sorts and paginates real table rows',()=>{
    show([{type:'table',title:'合成表',columns:[{key:'name',label:'名称'},{key:'count',label:'数量'}],rows:Array.from({length:25},(_,i)=>({name:`样本${i+1}`,count:25-i}))}]);
    expect(screen.getByText('第 1 / 2 页')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'下一页'}));
    expect(screen.getByText('样本25')).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox',{name:'合成表筛选'}),{target:{value:'样本25'}});
    expect(screen.getByText('显示 1 / 25 条')).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox',{name:'合成表筛选'}),{target:{value:''}});
    fireEvent.click(screen.getByRole('button',{name:'数量'}));
    expect(within(screen.getAllByRole('row')[1]).getByText('样本25')).toBeTruthy();
    fireEvent.change(screen.getByRole('textbox',{name:'合成表筛选'}),{target:{value:'不存在'}});
    expect(screen.getByText('没有匹配的记录')).toBeTruthy();
  });
  it('selects chart values including zero and negative numbers',()=>{
    show([{type:'chart',title:'统计',series:[{label:'正值',value:4},{label:'负值',value:-2},{label:'零',value:0}]}]);
    fireEvent.click(screen.getByRole('button',{name:'负值-2'}));
    expect(screen.getByRole('status').textContent).toContain('负值：-2');
    fireEvent.click(screen.getByRole('button',{name:'零0'}));
    expect(screen.getByRole('status').textContent).toContain('零：0');
  });
  it('selects map coordinates with keyboard without inventing boundaries',()=>{
    show([{type:'map',title:'分布',precision:'省级示意',points:[{label:'合成省',lat:30,lon:110,count:3}]}]);
    fireEvent.keyDown(screen.getByRole('button',{name:'合成省：3'}),{key:'Enter'});
    expect(screen.getByRole('status').textContent).toContain('纬度 30 · 经度 110 · 数量 3');
    expect(screen.getByText(/非行政区底图/)).toBeTruthy();
  });
  it('renders evidence, image alt and metrics as escaped content',()=>{
    show([{type:'evidence',items:[{quote:'<script>bad()</script>',feature:'喙',value:'弯曲',status:'待核验',source:'合成段落'}]},{type:'image',media_type:'image/png',content_base64:'aGVsbG8=',alt:'合成图片'},{type:'metrics',items:[{label:'记录数',value:0,unit:'条'}]}]);
    expect(screen.getByText('<script>bad()</script>')).toBeTruthy();
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByRole('img',{name:'合成图片'}).getAttribute('src')).toBe('data:image/png;base64,aGVsbG8=');
    expect(screen.getByText('0 条')).toBeTruthy();
  });
  it('uses an attached same-origin server download link instead of a Blob',()=>{
    const create=vi.fn();Object.defineProperty(URL,'createObjectURL',{configurable:true,value:create});
    const url='/api/v1/invocations/'+'a'.repeat(32)+'/workspace/0/2/download';
    show([{type:'download',filename:'结果.csv',media_type:'text/csv',download_url:url}]);
    const link=screen.getByRole('link',{name:'下载 结果.csv'}) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe(url);expect(link.download).toBe('结果.csv');
    expect(document.body.contains(link)).toBe(true);expect(create).not.toHaveBeenCalled();
  });
  it.each([undefined,'https://remote.example/private','javascript:alert(1)','//remote.example/private','/api/v1/models','/api/v1/invocations/'+'a'.repeat(32)+'/workspace/0/0/download?token=secret'])('rejects absent or non-platform download URLs (%s)',url=>{
    show([{type:'download',filename:'bad.txt',media_type:'text/plain',content_base64:'YWJj',download_url:url}]);
    expect(screen.queryByRole('link')).toBeNull();expect(screen.getByRole('alert').textContent).toContain('平台下载链接');
  });
  it('denies missing and offline action capabilities',()=>{
    const ctx=context();ctx.capabilities=[{...capability,availability:{state:'offline',available:false,message:'离线'}}];
    show([{type:'actions',items:[{label:'离线操作',capability_id:capability.id,input:{}},{label:'无权操作',capability_id:'other.private',input:{}}]}],ctx);
    for(const label of ['离线操作','无权操作'])expect((screen.getByRole('button',{name:label}) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
  });
  it('prefills real action refs, requires confirmation, invokes once and clears drafts',async()=>{
    vi.mocked(post).mockResolvedValue({id:'inv',summary:'已提交',run_id:'run',views:[]});
    const ctx=show([{type:'actions',items:[{label:'办理任务',capability_id:capability.id,input:{task_id:'task-real',expected_revision:3}}]}]);
    fireEvent.click(screen.getByRole('button',{name:'办理任务'}));
    await waitFor(()=>expect((screen.getByLabelText(/任务编号/) as HTMLInputElement).value).toBe('task-real'));
    fireEvent.click(screen.getByRole('button',{name:'预览操作'}));expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
    await waitFor(()=>expect(ctx.onResult).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledWith('/invoke',expect.objectContaining({capability_id:'synthetic.next',origin:'conversation',conversation_id:'conversation-a',input:{task_id:'task-real',expected_revision:3}}));
    expect(ctx.onFormInputChange).toHaveBeenCalledWith(capability.id,{});
  });
  it('renders unsupported blocks without executing remote code',()=>{
    show([{type:'html',html:'<script>bad()</script>'}]);expect(screen.getByText('此内容类型尚不受支持。')).toBeTruthy();expect(document.querySelector('script')).toBeNull();
  });
  it('shows declared full flow including blocked approval and exact skill command',()=>{
    const first=render(<AgentFlowView data={{status:'blocked',current_step:1,title:'申请',steps:[{id:'draft',title:'填报',status:'completed'},{id:'approve',title:'审批',status:'current'}]}}/>);
    expect(screen.getByText('等待授权角色办理')).toBeTruthy();expect(screen.getByText('✓ 填报')).toBeTruthy();expect(screen.getByText('审批')).toBeTruthy();
    first.rerender(<AgentFlowView data={{status:'catalog',skills:[{title:'申请',command:'/skill research/application'}]}}/>);
    expect(screen.getByText('/skill research/application')).toBeTruthy();
  });
});

describe('platform file form',()=>{
  const fileCap={...capability,input_schema:{type:'object',properties:{file:{type:'object',format:'platform-file',title:'上传表格',properties:{filename:{type:'string'},media_type:{type:'string'},content_base64:{type:'string'}},required:['filename','media_type','content_base64']}},required:['file'],additionalProperties:false}} as Capability;
  it('reads upload bytes and submits through the same confirmed capability path',async()=>{
    vi.mocked(post).mockResolvedValue({id:'inv',summary:'合成上传完成',views:[]});
    render(<CapabilityForm capability={fileCap} onResult={vi.fn()}/>);
    const file=new File(['a,b\n1,2'],'synthetic.csv',{type:'text/csv'});Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode('a,b\n1,2').buffer});
    fireEvent.change(screen.getByLabelText('上传表格'),{target:{files:[file]}});
    await screen.findByText('已选择：synthetic.csv');
    fireEvent.click(screen.getByRole('button',{name:'预览操作'}));
    await screen.findByText(/文件已选择；提交时上传正文/);
    expect(screen.queryByText(btoa('a,b\n1,2'))).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'确认执行'}));
    await waitFor(()=>expect(post).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledWith('/invoke',expect.objectContaining({input:{file:{filename:'synthetic.csv',media_type:'text/csv',content_base64:btoa('a,b\n1,2')}}}));
  });
  it('rejects empty and oversized files before invoking',async()=>{
    render(<CapabilityForm capability={fileCap} onResult={vi.fn()}/>);
    const input=screen.getByLabelText('上传表格');
    fireEvent.change(input,{target:{files:[new File([],'empty.csv')]}});
    expect((await screen.findByRole('alert')).textContent).toContain('4 MiB');
    const tooLarge=new File(['x'],'large.csv');Object.defineProperty(tooLarge,'size',{value:4*1024*1024+1});
    fireEvent.change(input,{target:{files:[tooLarge]}});expect(post).not.toHaveBeenCalled();
  });
});
