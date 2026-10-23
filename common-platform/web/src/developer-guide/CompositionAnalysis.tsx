import { useState } from 'react';
import { ArrowRight, ChevronDown, LayoutTemplate, MessageCircle } from 'lucide-react';
import { COMPOSITION_SOURCES, DESIGN, EXPOSURES, SUPPORT } from './composition';

function Evidence({ paths }: { paths: string[] }) {
  return <details className="ar-sources"><summary>查看代码依据<ChevronDown size={15}/></summary><ul>{paths.map(path=><li key={path}><code>{path}</code></li>)}</ul></details>;
}
type DemoResult = { module:string; id:string; input:string; output:string };
function ChainDemo() {
  const [surface,setSurface]=useState('page');
  const [results,setResults]=useState<DemoResult[]>([]);
  const [denied,setDenied]=useState(false);
  const [notice,setNotice]=useState('');
  const [title,setTitle]=useState('周末分享会资料');
  const modules=['资料登记','内容整理','结果发布'];
  function execute() {
    const step=results.length;
    if(step>=3) return;
    if(step===1&&denied) { setNotice('内容整理模块拒绝本次操作：当前用户没有权限。登记结果保留，发布步骤没有执行。'); return; }
    const values:DemoResult[]=[
      {module:modules[0],id:'A-001',input:title.trim(),output:'资料编号 A-001 · 第 1 版'},
      {module:modules[1],id:'B-001',input:'来源资料 A-001 · 第 1 版',output:'整理结果 B-001 · 保留来源 A-001'},
      {module:modules[2],id:'C-001',input:'整理结果 B-001',output:'发布记录 C-001 · 保留来源 B-001'},
    ];
    setResults(previous=>[...previous,values[step]]);
    setNotice(`${modules[step]}已完成。${step<2?'只把约定的结果交给下一步。':'三个模块各自保留记录，组合记录保留它们的关系。'}`);
  }
  return <section className="ca-demo"><p className="ar-demo-note">工作方式示意：三个模块为举例，通用组合执行器尚未实现。本例只解释建议方案，不调用实际接口。</p>
    <div className="ar-switch" role="group" aria-label="组合业务呈现方式"><button aria-pressed={surface==='page'} onClick={()=>setSurface('page')}><LayoutTemplate size={16}/>页面方式</button><button aria-pressed={surface==='conversation'} onClick={()=>setSurface('conversation')}><MessageCircle size={16}/>对话方式</button></div>
    <p className="ca-run">同一个示例组合：COM-001 · 已完成 {results.length} / 3 步</p>
    <div className="ca-chain" aria-label="模块之间的数据连接">{modules.map((name,index)=><div key={name}><span>{index+1}</span><strong>{name}模块</strong><small>{index===0?'产生资料编号':index===1?'读取资料编号，产生整理结果':'读取整理结果，产生发布记录'}</small><em>{results[index]?'已完成':index===results.length?'当前步骤':'等待前一步'}</em></div>)}</div>
    <div className={'ca-stage '+surface} aria-label="组合业务示例内容">
      {surface==='conversation'&&<p className="ca-message">{results.length===3?'三个模块已处理完毕，可以查看各步结果。':`接下来由${modules[results.length]}模块办理。之前已完成的内容继续保留。`}</p>}
      <div className="ca-results">{(surface==='page'?modules:modules.filter((_,index)=>index===Math.min(results.length,2))).map(name=>{const index=modules.indexOf(name);const result=results[index];return <article key={name}><h3>{name}模块</h3><p>{result?result.output:index===results.length?'等待本次确认后执行。':'等待前一步提供结果。'}</p>{result&&<small>本步使用的输入：{result.input}</small>}</article>;})}</div>
      {surface==='conversation'&&results.length>0&&<details className="ar-sources"><summary>查看之前的结果<ChevronDown size={15}/></summary><ul>{results.map(result=><li key={result.id}>{result.module}：{result.output}</li>)}</ul></details>}
      {results.length===0&&<label className="ca-title">资料名称<input value={title} onChange={event=>setTitle(event.target.value)}/></label>}
      <label className="ca-permission"><input type="checkbox" checked={denied} disabled={results.length>1} onChange={event=>{setDenied(event.target.checked);setNotice('');}}/>模拟当前用户没有整理权限</label>
      <div className="ar-demo-controls"><button className="ar-secondary" onClick={()=>{setResults([]);setNotice('');setDenied(false);}}>重置串联示例</button><button className="ar-primary" disabled={results.length===3||!title.trim()} onClick={execute}>{results.length===3?'示例组合已完成':`执行${modules[results.length]}`}<ArrowRight size={16}/></button></div>
      {notice&&<p className="ar-outcome" role="status">{notice}</p>}
    </div><p className="ar-caption">切换页面与对话，共用同一份示例进展。刷新会重新开始；真实组合的持久执行与恢复仍需实现。</p>
  </section>;
}
export default function CompositionAnalysis() {
  const [tab,setTab]=useState(0);
  const [selected,setSelected]=useState('organization');
  const [design,setDesign]=useState(0);
  const item=SUPPORT.find(value=>value.id===selected)!;
  return <><div className="ar-heading"><span className="fg-kicker">核心分析 · 模块公开，总框架组织</span><h1>怎样把多个模块组成一项业务？</h1><p>总框架管理微服务和统一认证；模块公开业务行为、数据与接口，总框架按明确规则连接。组合后的业务也同时使用页面和对话。</p></div>
    <div className="ca-conclusion"><strong>当前结论</strong><p>已具备统一登记、调用、身份和共享界面的基础，也能用明确参数接续另一个模块。完整的自由组织还需要组合定义、数据连接、多记录关联和组合执行记录。</p></div>
    <div className="ar-switch ca-tabs" role="group" aria-label="业务组织分析内容">{['当前支持程度','模块要公开什么','主框架怎样组织','串联示例'].map((name,index)=><button key={name} aria-pressed={tab===index} onClick={()=>setTab(index)}>{name}</button>)}</div>
    {tab===0?<div className="ca-support"><nav aria-label="选择支持维度">{SUPPORT.map(value=><button key={value.id} aria-pressed={selected===value.id} onClick={()=>setSelected(value.id)}><strong>{value.title}</strong><span className={'ca-level '+(value.level==='需要新增'?'missing':value.level==='部分支持'?'partial':'')}>{value.level}</span></button>)}</nav><section aria-label="当前支持说明" aria-live="polite"><h2>{item.title}</h2><dl><div><dt>当前可以</dt><dd>{item.current}</dd></div><div><dt>限制在哪</dt><dd>{item.limit}</dd></div><div><dt>还需补充</dt><dd>{item.next}</dd></div></dl><Evidence paths={item.sources}/></section></div>
    :tab===1?<section className="ca-exposure"><p className="ar-demo-note">以下是模块应提供的完整说明；其中已有功能格式可复用，新增业务含义和组合字段需要先定义并实现。</p>{EXPOSURES.map(value=><article key={value.title}><h2>{value.title}</h2><p>{value.text}</p><p className="ar-example">{value.example}</p></article>)}</section>
    :tab===2?<><div className="ar-life"><nav aria-label="组合方案的组成">{DESIGN.map((value,index)=><button key={value.title} aria-pressed={design===index} onClick={()=>setDesign(index)}><span>{index+1}</span>{value.title}</button>)}</nav><section aria-live="polite"><span className="ar-eyebrow">建议方案 · {DESIGN[design].owner}</span><h2>{DESIGN[design].title}</h2><p>{DESIGN[design].text}</p><p className="ar-outcome good">{DESIGN[design].outcome}</p></section></div><div className="ca-acceptance"><h2>先做一个能验证的最小组合</h2><p>三个实际模块按顺序办理，明确传递编号与版本；中途换到对话继续。再验证目标无权限、数据变更、服务离线、重复提交和超时后怎样处理。</p><p>先完成固定顺序、明确字段连接与执行记录，再增加条件分支、人工等待和并行。当前没有通用跨服务回滚。</p></div></>
    :<ChainDemo/>}
    <Evidence paths={COMPOSITION_SOURCES}/>
  </>;
}
