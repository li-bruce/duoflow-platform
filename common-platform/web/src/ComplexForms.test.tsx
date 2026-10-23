// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { JsonSchema7 as JsonSchema } from '@jsonforms/core';
import type { Capability } from './types';
import CapabilityForm from './CapabilityForm';
import { post } from './api';

vi.mock('./api',()=>({post:vi.fn(),api:vi.fn().mockResolvedValue({items:[]}),requestId:()=> 'synthetic-complex',errorText:(error:Error)=>error.message}));
const fileSchema:JsonSchema={type:'object',format:'platform-file',properties:{filename:{type:'string'},media_type:{type:'string'},content_base64:{type:'string'}},required:['filename','media_type','content_base64'],additionalProperties:false};
function show(properties:Record<string,JsonSchema>,required:string[]=[],initialInput?:Record<string,unknown>){
  const capability:Capability={id:'synthetic.complex',module_id:'synthetic',version:'1',title:'复杂输入',description:'合成测试',kind:'command',roles:['researcher'],input_schema:{type:'object',properties,required,additionalProperties:false},output_schema:{type:'object'},execution:{mode:'background',confirmation:true},presentation:{page:{renderer:'workspace'},conversation:{renderer:'workspace'}}};
  return render(<CapabilityForm capability={capability} initialInput={initialInput} onResult={vi.fn()}/>);
}
function syntheticFile(name:string,text='synthetic',type='image/png'){
  const file=new File([text],name,{type});Object.defineProperty(file,'arrayBuffer',{value:async()=>new TextEncoder().encode(text).buffer});return file;
}
async function confirm(){fireEvent.click(screen.getByRole('button',{name:'预览操作'}));fireEvent.click(await screen.findByRole('button',{name:'确认执行'}));await waitFor(()=>expect(post).toHaveBeenCalledOnce());return (vi.mocked(post).mock.calls[0][1] as {input:Record<string,unknown>}).input;}
beforeEach(()=>{vi.mocked(post).mockReset();vi.mocked(post).mockResolvedValue({id:'invocation',summary:'合成成功',views:[]});});
afterEach(()=>{cleanup();vi.restoreAllMocks();});

describe('complex fields through actual JSON Forms',()=>{
  it('shows explicit enum option text and submits strings, zero and false without coercion',async()=>{
    show({kind:{type:'string',title:'合成类别',enum:['annual','专项合成']},count:{type:'integer',title:'合成数量',enum:[0,2]},enabled:{type:'boolean',title:'合成开关',enum:[false,true]}},['kind','count','enabled'],{kind:'annual',count:0,enabled:false});
    const kind=screen.getByRole('combobox',{name:'合成类别 *'}) as HTMLSelectElement;
    expect(Array.from(kind.options,option=>option.textContent)).toEqual(['请选择','annual','专项合成']);
    expect(kind.selectedOptions[0].textContent).toBe('annual');
    expect((screen.getByRole('combobox',{name:'合成数量 *'}) as HTMLSelectElement).value).toBe('0');
    expect((screen.getByRole('combobox',{name:'合成开关 *'}) as HTMLSelectElement).value).toBe('false');
    expect(screen.getByRole('option',{name:'专项合成'}).textContent).toBe('专项合成');
    fireEvent.change(kind,{target:{value:JSON.stringify('专项合成')}});
    fireEvent.change(screen.getByRole('combobox',{name:'合成数量 *'}),{target:{value:'0'}});
    fireEvent.change(screen.getByRole('combobox',{name:'合成开关 *'}),{target:{value:'false'}});
    expect(await confirm()).toEqual({kind:'专项合成',count:0,enabled:false});
  });

  it('shows invalid choice errors and disables choices during confirmation',async()=>{
    show({mode:{type:'string',title:'合成模式',enum:['第一模式','第二模式']}},['mode'],{mode:'旧版失效值'});
    const select=screen.getByRole('combobox',{name:'合成模式 *'}) as HTMLSelectElement;
    fireEvent.click(screen.getByRole('button',{name:'预览操作'}));
    expect(await screen.findByText('请选择有效选项')).toBeTruthy();
    expect(screen.queryByRole('button',{name:'确认执行'})).toBeNull();
    expect(select.disabled).toBe(false);
    fireEvent.change(select,{target:{value:JSON.stringify('第一模式')}});
    fireEvent.click(screen.getByRole('button',{name:'预览操作'}));
    await screen.findByRole('button',{name:'确认执行'});
    expect(select.disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'继续编辑'}));
    expect(select.disabled).toBe(false);
    expect(select.selectedOptions[0].textContent).toBe('第一模式');
  });

  it('shows oneOf titles and value fallbacks, including nested typed choices',async()=>{
    show({lane:{title:'办理分支',oneOf:[{const:'annual',title:'年度合成流程'},{const:'special',title:'专项合成流程'}]},settings:{type:'object',title:'设置',additionalProperties:false,properties:{level:{title:'级别',oneOf:[{const:0,title:'零级'},{const:2}]},empty:{title:'空值选择',oneOf:[{const:null,title:'不指定'},{const:'',title:'保留空字符串'}]}},required:['level','empty']}},['lane','settings']);
    const lane=screen.getByRole('combobox',{name:'办理分支 *'}) as HTMLSelectElement;
    expect(Array.from(lane.options,option=>option.textContent)).toEqual(['请选择','年度合成流程','专项合成流程']);
    expect(screen.getByRole('option',{name:'2'}).textContent).toBe('2');
    fireEvent.change(lane,{target:{value:JSON.stringify('special')}});
    fireEvent.change(screen.getByRole('combobox',{name:'级别 *'}),{target:{value:'0'}});
    fireEvent.change(screen.getByRole('combobox',{name:'空值选择 *'}),{target:{value:'null'}});
    expect(await confirm()).toEqual({lane:'special',settings:{level:0,empty:null}});
  });

  it('keeps structured oneOf alternatives as JSON input rather than inventing choice values',async()=>{
    show({payload:{title:'复合输入',oneOf:[{type:'object',properties:{name:{type:'string'}},required:['name'],additionalProperties:false},{type:'array',items:{type:'number'}}]}},['payload']);
    expect(screen.queryByRole('combobox',{name:/复合输入/})).toBeNull();
    fireEvent.change(screen.getByRole('textbox',{name:'复合输入（JSON）'}),{target:{value:'{"name":"合成对象"}'}});
    expect(await confirm()).toEqual({payload:{name:'合成对象'}});
  });

  it('reads multiple files and immediately submits their bytes through one confirmation',async()=>{
    show({images:{type:'array',title:'图片文件',items:fileSchema,minItems:1,maxItems:2}},['images']);
    const input=screen.getByLabelText('图片文件') as HTMLInputElement;expect(input.multiple).toBe(true);
    fireEvent.change(input,{target:{files:[syntheticFile('a.png','a'),syntheticFile('b.png','b')]}});
    await screen.findByText('已选择：b.png');
    const values=await confirm();
    expect(values.images).toEqual([{filename:'a.png',media_type:'image/png',content_base64:btoa('a')},{filename:'b.png',media_type:'image/png',content_base64:btoa('b')}]);
    expect(screen.queryByText(btoa('b'))).toBeNull();
  });

  it('removes individual files and clears selection instead of retaining old input',async()=>{
    show({images:{type:'array',title:'图片文件',items:fileSchema,maxItems:2}},[]);
    fireEvent.change(screen.getByLabelText('图片文件'),{target:{files:[syntheticFile('a.png'),syntheticFile('b.png')]}});
    await screen.findByText('已选择：b.png');
    fireEvent.click(screen.getByRole('button',{name:'移除 a.png'}));
    expect(screen.queryByText('已选择：a.png')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'清空图片文件'}));
    expect(screen.queryByText('已选择：b.png')).toBeNull();
    expect((await confirm()).images).toEqual([]);
  });

  it('blocks empty, oversized and too many files including replacement of a valid selection',async()=>{
    show({images:{type:'array',title:'图片文件',items:fileSchema,maxItems:2}},[]);
    const input=screen.getByLabelText('图片文件');
    fireEvent.change(input,{target:{files:[syntheticFile('good.png')]}});await screen.findByText('已选择：good.png');
    fireEvent.change(input,{target:{files:[new File([],'empty.png')]}});
    expect((await screen.findByRole('alert')).textContent).toContain('4 MiB');
    expect(screen.queryByText('已选择：good.png')).toBeNull();
    expect((screen.getByRole('button',{name:'预览操作'}) as HTMLButtonElement).disabled).toBe(true);
    const large=syntheticFile('large.png');Object.defineProperty(large,'size',{value:4*1024*1024+1});
    fireEvent.change(input,{target:{files:[large]}});expect(screen.getByRole('alert').textContent).toContain('4 MiB');
    fireEvent.change(input,{target:{files:[syntheticFile('a.png'),syntheticFile('b.png'),syntheticFile('c.png')]}});
    expect(screen.getByRole('alert').textContent).toContain('最多选择 2');
    fireEvent.submit(input.closest('form')!);expect(post).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'确认执行'})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'清空图片文件'}));expect((screen.getByRole('button',{name:'预览操作'}) as HTMLButtonElement).disabled).toBe(false);
  });

  it('blocks submission during reading and discards a late file read after reset',async()=>{
    show({file:{...fileSchema,title:'上传文件'}},['file']);
    let resolve!:(value:ArrayBuffer)=>void;const pending=new Promise<ArrayBuffer>(done=>{resolve=done;});
    const file=new File(['waiting'],'late.png');Object.defineProperty(file,'arrayBuffer',{value:()=>pending});
    fireEvent.change(screen.getByLabelText('上传文件'),{target:{files:[file]}});
    expect((screen.getByRole('button',{name:'预览操作'}) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button',{name:'重置表单'}));
    await act(async()=>{resolve(new TextEncoder().encode('late').buffer);await pending;});
    expect(screen.queryByText('已选择：late.png')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'预览操作'}));expect(screen.queryByRole('button',{name:'确认执行'})).toBeNull();expect(post).not.toHaveBeenCalled();
  });

  it('opens nullable package JSON, preserves nested values, and returns explicit null when cleared',async()=>{
    const schema:JsonSchema={title:'数据包',default:null,anyOf:[{type:'object',additionalProperties:true},{type:'null'}]};
    show({package:schema});
    expect(screen.queryByRole('textbox',{name:'数据包（JSON）'})).toBeNull();
    fireEvent.click(screen.getByRole('checkbox',{name:'填写数据包'}));
    fireEvent.change(screen.getByRole('textbox',{name:'数据包（JSON）'}),{target:{value:'{"records":[{"number":1,"value":null}],"meta":{"source":"合成"}}'}});
    expect((await confirm()).package).toEqual({records:[{number:1,value:null}],meta:{source:'合成'}});
    await waitFor(()=>expect(screen.getByRole('button',{name:'预览操作'})).toBeTruthy());
    vi.mocked(post).mockClear();fireEvent.click(screen.getByRole('checkbox',{name:'填写数据包'}));
    expect((await confirm()).package).toBeNull();
  });

  it('never submits previous valid JSON while the visible editor contains a syntax error',async()=>{
    show({mapping:{type:'object',title:'字段映射',additionalProperties:{type:'string'}},rows:{type:'array',title:'记录列表',items:{type:'object'}}},['mapping']);
    const editor=screen.getByRole('textbox',{name:'字段映射（JSON）'});
    fireEvent.change(editor,{target:{value:'{"old":"valid"}'}});
    fireEvent.change(editor,{target:{value:'{"new":'}});
    expect(screen.getByRole('alert').textContent).toContain('JSON 格式不正确');
    fireEvent.change(screen.getByRole('textbox',{name:'记录列表（JSON）'}),{target:{value:'[{"id":1},{"id":2}]'}});
    expect((editor as HTMLTextAreaElement).value).toBe('{"new":');
    fireEvent.submit(editor.closest('form')!);expect(post).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'确认执行'})).toBeNull();
    fireEvent.change(editor,{target:{value:'{"new":"corrected"}'}});
    expect(await confirm()).toEqual({mapping:{new:'corrected'},rows:[{id:1},{id:2}]});
  });

  it('checks dynamic value types and array bounds using the actual form validator',async()=>{
    show({mapping:{type:'object',title:'字段映射',additionalProperties:{type:'string'}},rows:{type:'array',title:'记录列表',minItems:1,maxItems:2,items:{type:'integer'}}},['mapping','rows']);
    fireEvent.change(screen.getByRole('textbox',{name:'字段映射（JSON）'}),{target:{value:'{"field":123}'}});
    fireEvent.change(screen.getByRole('textbox',{name:'记录列表（JSON）'}),{target:{value:'[1,2,3]'}});
    fireEvent.click(screen.getByRole('button',{name:'预览操作'}));expect(screen.queryByRole('button',{name:'确认执行'})).toBeNull();
    fireEvent.change(screen.getByRole('textbox',{name:'字段映射（JSON）'}),{target:{value:'{"field":"name"}'}});
    fireEvent.change(screen.getByRole('textbox',{name:'记录列表（JSON）'}),{target:{value:'[1,2]'}});
    expect(await confirm()).toEqual({mapping:{field:'name'},rows:[1,2]});
  });

  it('renders nested typed fields and nullable numbers as normal controls',async()=>{
    show({settings:{type:'object',title:'规则设置',additionalProperties:false,properties:{name:{type:'string',title:'规则名',minLength:1},limit:{title:'数量上限',type:['integer','null'],minimum:1},enabled:{type:'boolean',title:'启用规则'},mode:{enum:['strict','loose'],title:'校验模式'}},required:['name','limit','mode']}},['settings']);
    fireEvent.change(screen.getByLabelText(/规则名/),{target:{value:'合成规则'}});
    fireEvent.click(screen.getByRole('checkbox',{name:'填写数量上限'}));
    fireEvent.change(screen.getByLabelText(/数量上限 \*/),{target:{value:'3'}});
    fireEvent.click(screen.getByRole('checkbox',{name:'启用规则'}));
    fireEvent.change(screen.getByLabelText(/校验模式/),{target:{value:JSON.stringify('strict')}});
    expect(await confirm()).toEqual({settings:{name:'合成规则',limit:3,enabled:true,mode:'strict'}});
  });

  it('resets malformed JSON and file errors instead of leaving invisible submission blockers',async()=>{
    show({package:{type:'object',title:'数据包'},file:{...fileSchema,title:'上传文件'}});
    fireEvent.change(screen.getByRole('textbox',{name:'数据包（JSON）'}),{target:{value:'invalid json'}});
    fireEvent.change(screen.getByLabelText('上传文件'),{target:{files:[new File([],'empty')]}});
    fireEvent.click(screen.getByRole('button',{name:'重置表单'}));
    expect(screen.queryAllByRole('alert')).toHaveLength(0);
    expect(await confirm()).toEqual({});
  });
});
