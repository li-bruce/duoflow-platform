import { useEffect, useReducer, useRef, useState } from 'react';
import type { Dispatch, FormEvent } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, BookOpen, Check, CheckCircle2, ChevronDown, ClipboardList, FileText, LayoutTemplate, MessageCircle, Plus, RotateCcw, ShieldCheck, SquarePen } from 'lucide-react';
import { ACTIONS, EXAMPLE_BODY, guideReducer, initialGuide, operationError, restoreGuide, STATUS, STORAGE_KEY } from './guideState';
import type { Chapter, GuideAction, GuideState, Surface } from './guideState';
import './guide.css';

const CHAPTERS = ['认识这个框架', '亲手试一遍', '了解怎样配合', '接入自己的功能'];
const STEPS = ['填写事项', '换到对话并提交', '退回并写明原因', '按意见修改', '再次提交', '确认完成'];
const GUIDANCE = [
  ['先写下要做的事', '我们用一场读书分享会做练习。事项名称和说明已经填好，你也可以改成自己的内容。', '创建后，这件事有了可以继续填写和提交的记录。'],
  ['换到对话，接着提交', '刚才在页面中创建的事项已经保存。试着换到对话，看看同一份内容是否还在，然后提交它。', '换个入口不会重新创建事项，也不会自动提交。'],
  ['发现遗漏，退回补充', '说明里还缺少时间和地点。现在体验处理者的操作：写明需要补充什么，再退回。', '退回时留下原因，填写者才能知道怎样修改。'],
  ['看着意见，补全说明', '你已经收到了退回意见。在原来的事项中补充时间和地点，保存修改即可。', '修改和重新提交分开进行，可以先把内容写完整。'],
  ['检查内容，再次提交', '修改已经保存。确认现在的说明包含了需要的信息，再提交一次。', '记录会保留之前的退回和这次修改，方便回看。'],
  ['确认这件事已经完成', '现在内容已经补齐。填写处理说明，再把事项标记为完成。', '完成后可以查看整个过程；切换页面或刷新都不会把它变回草稿。'],
  ['一件事，完整办完了', '从创建到完成，你始终在处理同一件事。页面和对话只是两种进入方式。', '接下来看看：哪些事情由框架负责，哪些由你自己的功能负责。'],
];

function SurfaceSwitch({ value, onChange }: { value: Surface; onChange: (value: Surface) => void }) {
  return <div className="fg-surface-switch" role="group" aria-label="选择办理方式">
    <button type="button" aria-pressed={value === 'page'} onClick={() => onChange('page')}><LayoutTemplate size={17}/>在页面中</button>
    <button type="button" aria-pressed={value === 'conversation'} onClick={() => onChange('conversation')}><MessageCircle size={17}/>在对话中</button>
  </div>;
}

function Introduction() {
  const [surface, setSurface] = useState<Surface>('page');
  return <>
    <div className="fg-intro">
      <div className="fg-intro-copy">
        <span className="fg-kicker">从一件小事开始</span>
        <h1>把一件事，<br/>从头办到尾。</h1>
        <p className="fg-lead">在页面里填写，在对话里继续。<br/>换一种操作方式，事情的进展依然接得上。</p>
        <p className="fg-intro-explain">这个框架把登录、功能入口和处理记录组织在一起。你把要做的事情接进来，就能用页面和对话两种方式办理。</p>
        <div className="fg-small-path"><span>填写</span><ArrowRight/><span>提交</span><ArrowRight/><span>修改</span><ArrowRight/><span>完成</span></div>
      </div>
      <div className="fg-illustration">
        <div className="fg-illustration-heading"><span className="fg-ink-dot"/>同一件事，两种打开方式</div>
        <SurfaceSwitch value={surface} onChange={setSurface}/>
        <div className={'fg-preview-frame ' + surface}>
          <div className="fg-window-top"><span/><span/><span/><small>{surface === 'page' ? '事项页面' : '事项对话'}</small></div>
          <div className="fg-preview-body" aria-live="polite">
            {surface === 'conversation' && <div className="fg-message">继续办理刚才的分享会。</div>}
            <div className="fg-paper">
              <div className="fg-paper-top"><span><FileText size={17}/>分享会准备</span><span className="fg-status">草稿</span></div>
              <h3>准备一次读书分享会</h3>
              <p>邀请同事分享最近读过的一本书。</p>
              <div className="fg-paper-bottom"><CheckCircle2 size={15}/>已保存，可以继续填写</div>
            </div>
          </div>
        </div>
        <p className="fg-illustration-note"><span className="fg-short-line"/>试着切换上方按钮，内容始终是同一份。</p>
      </div>
    </div>
    <div className="fg-takeaways">
      <div><MessageCircle/><h2>换个入口，继续办理</h2><p>页面适合集中填写，对话适合一步一步处理。</p></div>
      <div><ClipboardList/><h2>知道事情办到了哪里</h2><p>草稿、提交、退回和完成，都有清楚的记录。</p></div>
      <div><Plus/><h2>接入你自己的功能</h2><p>报修、资料审核、活动准备，都可以按各自规则设计。</p></div>
    </div>
  </>;
}

function PracticeRecord({ state, dispatch }: { state: GuideState; dispatch: Dispatch<GuideAction> }) {
  const [error, setError] = useState('');
  const editable = state.step === 0 || state.step === 3;
  const processing = state.step === 2 || state.step === 5;
  const mustSwitch = state.step === 1 && state.surface === 'page';
  useEffect(() => { setError(''); }, [state.step, state.surface]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (mustSwitch) { dispatch({ type: 'surface', surface: 'conversation' }); return; }
    const message = operationError(state);
    setError(message);
    if (!message) dispatch({ type: 'operate' });
  }
  return <form className="fg-record" onSubmit={submit}>
    <div className="fg-record-heading"><span><FileText size={17}/>{state.step === 0 ? '一件新的事项' : '你的分享会事项'}</span><span className={'fg-status step-' + state.step}>{STATUS[state.step]}</span></div>
    {editable ? <>
      <label className="fg-field">事项名称<input value={state.title} maxLength={200} onChange={e => dispatch({ type: 'field', field: 'title', value: e.target.value })}/></label>
      {state.step === 3 && <div className="fg-feedback" role="note" aria-label="退回意见"><strong>退回意见</strong><p>{state.feedback}</p></div>}
      <label className="fg-field">事项说明<textarea rows={state.step === 3 ? 5 : 3} value={state.body} maxLength={4000} onChange={e => dispatch({ type: 'field', field: 'body', value: e.target.value })}/></label>
      {state.step === 3 && <button type="button" className="fg-text-button" onClick={() => dispatch({ type: 'field', field: 'body', value: EXAMPLE_BODY })}>填入示例时间和地点<ArrowUpRight size={14}/></button>}
    </> : <div className="fg-record-content"><h3>{state.saved?.title}</h3><p>{state.saved?.body}</p></div>}
    {processing && <label className="fg-field">{state.step === 2 ? '退回原因' : '完成说明'}<textarea rows={2} maxLength={1000} value={state.note} onChange={e => dispatch({ type: 'field', field: 'note', value: e.target.value })}/></label>}
    {error && <p role="alert" className="fg-error">{error}</p>}
    {state.step < 6 ? <button className="fg-button fg-primary fg-record-action" type="submit">{mustSwitch ? '在对话中继续' : ACTIONS[state.step]}<ArrowRight size={17}/></button>
      : <div className="fg-finished"><CheckCircle2 size={23}/><div><strong>这件事已经办完了</strong><p>你可以刷新页面，或换个入口查看结果。</p></div></div>}
    {state.history.length > 0 && <details className="fg-history"><summary>查看办理记录（{state.history.length}）<ChevronDown size={15}/></summary><ol>{state.history.map((entry, index) => <li key={index}><span>{index + 1}</span><div><strong>{entry.action}</strong>{entry.note && <p>{entry.note}</p>}</div></li>)}</ol></details>}
  </form>;
}

function Practice({ state, dispatch, storageAvailable }: { state: GuideState; dispatch: Dispatch<GuideAction>; storageAvailable: boolean }) {
  const [confirmReset, setConfirmReset] = useState(false);
  const guidance = GUIDANCE[state.step];
  return <>
    <div className="fg-section-heading"><div><span className="fg-kicker">边操作，边了解</span><h1>一起准备一场分享会。</h1><p>这是练习。由你依次体验填写和处理，内容只保存在当前浏览器。</p></div>
      <button className="fg-text-button" onClick={() => setConfirmReset(true)}><RotateCcw size={15}/>重新练习</button></div>
    {confirmReset && <div className="fg-reset-confirm" role="group" aria-label="重新练习确认"><p>清除这次练习，回到填写事项？</p><button className="fg-button fg-secondary" onClick={() => setConfirmReset(false)}>保留当前练习</button><button className="fg-button fg-primary" onClick={() => { dispatch({ type: 'restart' }); setConfirmReset(false); }}>清除并重新开始</button></div>}
    <div className="fg-practice-layout">
      <aside className="fg-coach">
        <div className="fg-coach-count">{state.step === 6 ? <CheckCircle2 size={20}/> : <span>{state.step + 1}</span>}<small>{state.step === 6 ? '练习已完成' : `共 ${STEPS.length} 步`}</small></div>
        <div aria-live="polite" aria-atomic="true"><h2>{guidance[0]}</h2><p>{guidance[1]}</p></div>
        <ol className="fg-step-list">{STEPS.map((label, index) => <li key={label} className={index < state.step ? 'done' : index === state.step ? 'current' : ''} aria-current={index === state.step ? 'step' : undefined}><span>{index < state.step ? <Check size={12}/> : index + 1}</span>{label}</li>)}</ol>
        <div className="fg-explanation"><BookOpen size={17}/><p>{guidance[2]}</p></div>
      </aside>
      <div className="fg-practice-area">
        <SurfaceSwitch value={state.surface} onChange={surface => dispatch({ type: 'surface', surface })}/>
        <div className={'fg-practice-window ' + state.surface}>
          <div className="fg-window-top"><span/><span/><span/><small>{state.surface === 'page' ? '事项页面' : '事项对话'} · 练习</small></div>
          <div className="fg-practice-window-body">
            {state.surface === 'conversation' && <div className="fg-chat-hint"><MessageCircle size={17}/><div><strong>办理提示</strong><p>{state.step === 1 ? '已找到你刚才创建的事项。确认内容后，可以在这里提交。' : guidance[0] + '。'}</p></div></div>}
            <PracticeRecord state={state} dispatch={dispatch}/>
          </div>
        </div>
        <p className="fg-persist-hint"><CheckCircle2 size={14}/>{storageAvailable ? '切换办理方式或刷新页面，可以接着上次的练习。' : '本次练习仍可继续；浏览器暂时无法保存进度。'}</p>
      </div>
    </div>
  </>;
}

const EXPLANATIONS = [
  { name: '打开一件事项', icon: FileText, you: '选择要继续办理的事项', framework: '确认你是谁，找到对应功能', business: '检查你能否查看，并取出已保存的内容', result: '显示事项内容和当前进展', note: '框架负责把入口连接起来，具体功能负责保存和提供这件事的内容。' },
  { name: '提交填写的内容', icon: SquarePen, you: '填写说明，点击提交', framework: '把这次操作交给对应功能，记录处理情况', business: '检查填写内容和办理条件，保存提交结果', result: '显示“已提交”和处理记录', note: '按钮只是发起操作。检查通过、结果保存后，这件事才真正变成“已提交”。' },
  { name: '换到对话继续', icon: MessageCircle, you: '在对话中继续这件事', framework: '找到同一件事项，显示相同的操作界面', business: '提供最新的内容和当前可以进行的操作', result: '接着办理，不用再填一遍', note: '页面和对话共用操作界面，也读取同一份事项。换入口不会重复提交。' },
];

function Cooperation() {
  const [selected, setSelected] = useState(0);
  const explanation = EXPLANATIONS[selected];
  return <>
    <div className="fg-section-heading"><div><span className="fg-kicker">把刚才的过程拆开看</span><h1>你做一件事，大家怎样配合？</h1><p>框架负责共同的部分，你接入的功能负责具体的事情。</p></div></div>
    <div className="fg-choice-row" role="group" aria-label="选择一个操作">{EXPLANATIONS.map((item, index) => <button key={item.name} aria-pressed={selected === index} onClick={() => setSelected(index)}><item.icon size={18}/>{item.name}</button>)}</div>
    <div className="fg-cooperation" aria-live="polite">
      <div className="fg-you"><span className="fg-person">你</span><div><span className="fg-label">从这里开始</span><h2>{explanation.you}</h2></div></div>
      <div className="fg-flow-line" aria-hidden="true"><span/><ArrowRight size={20}/></div>
      <div className="fg-partnership"><article><span className="fg-owner framework">框架负责</span><h3>{explanation.framework}</h3><p>登录、找到功能、连接页面和对话、记录操作过程。</p></article><div className="fg-partnership-link" aria-hidden="true"><ArrowRight size={19}/></div><article><span className="fg-owner business">你的功能负责</span><h3>{explanation.business}</h3><p>填写什么、谁能办理、怎样处理、保存哪些结果。</p></article></div>
      <div className="fg-result"><CheckCircle2 size={24}/><div><span className="fg-label">你看到的结果</span><h3>{explanation.result}</h3></div></div>
    </div>
    <p className="fg-bottom-note"><BookOpen size={18}/>{explanation.note}</p>
  </>;
}

const SCENARIOS = [
  { name: '活动准备', item: '一次活动', fields: '活动名称、时间、地点、准备说明', actions: '保存、提交、退回修改、确认完成', rule: '由发起人填写，指定的处理者检查；退回后可以补充，再次提交。', outcome: '一份活动安排，以及每一次修改和处理的记录。' },
  { name: '设备报修', item: '一张报修单', fields: '设备名称、所在位置、故障说明', actions: '提交报修、安排维修、填写维修结果、确认修好', rule: '报修人说明问题，维修人员记录处理结果，报修人确认是否修好。', outcome: '一份维修结果，以及从报修到修好的处理记录。' },
  { name: '资料审核', item: '一份待审资料', fields: '资料名称、用途、文件和补充说明', actions: '上传、提交审核、退回修改、审核通过', rule: '提交者准备资料，审核者检查；缺少内容时写明原因，退回补充。', outcome: '一份审核结论，以及提交、修改和审核的记录。' },
];

function OwnFeature() {
  const [selected, setSelected] = useState(0);
  const [kind, setKind] = useState<'tool' | 'process'>('process');
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const scenario = SCENARIOS[selected];
  const plan = `${scenario.name}\n办理对象：${scenario.item}\n填写信息：${scenario.fields}\n操作：${scenario.actions}\n办理规则：${scenario.rule}\n结果：${scenario.outcome}`;
  async function copy() {
    try { await navigator.clipboard.writeText(plan); setCopied(true); setCopyError(false); }
    catch { setCopyError(true); }
  }
  return <>
    <div className="fg-section-heading"><div><span className="fg-kicker">从示例走向自己的事情</span><h1>先说清楚，要办什么事。</h1><p>接入前先整理四件事，再用框架提供的模板开发和安装。</p></div></div>
    <div className="fg-scenario-row"><span>换个例子看看</span><div className="fg-choice-row" role="group" aria-label="选择自己的功能示例">{SCENARIOS.map((item, index) => <button key={item.name} aria-pressed={selected === index} onClick={() => { setSelected(index); setCopied(false); setCopyError(false); }}>{item.name}</button>)}</div></div>
    <div className="fg-planning-sheet" aria-live="polite">
      <div className="fg-sheet-heading"><div><span className="fg-label">围绕{scenario.item}</span><h2>{scenario.name}，需要准备什么？</h2></div><ClipboardList size={28}/></div>
      <div className="fg-plan-rows">{[
        ['填写哪些信息', scenario.fields], ['可以进行哪些操作', scenario.actions], ['谁来办，什么时候能办', scenario.rule], ['最后要得到什么', scenario.outcome],
      ].map(([title, content], index) => <div key={title}><span>{index + 1}</span><h3>{title}</h3><p>{content}</p></div>)}</div>
      <div className="fg-sheet-footer"><span>这里展示的是设计示例，需要开发后才能使用。</span><button className="fg-text-button" aria-label={copied ? '已复制这份说明' : '复制这份说明'} onClick={() => void copy()}>{copied ? <Check size={15}/> : <ClipboardList size={15}/>}<span role="status">{copied ? '已复制这份说明' : '复制这份说明'}</span></button></div>
      {copyError && <p className="fg-error" role="alert">浏览器暂时不能复制，请直接选择上方文字后复制。</p>}
    </div>
    <details className="fg-development-details"><summary><span>准备开发时，具体怎么接入？</span><ChevronDown size={19}/></summary>
      <div className="fg-development-content">
        <p>先看你的功能是哪一种：</p><div className="fg-choice-row" role="group" aria-label="选择功能类型"><button aria-pressed={kind === 'tool'} onClick={() => setKind('tool')}>填一次，就能得到结果</button><button aria-pressed={kind === 'process'} onClick={() => setKind('process')}>需要多次填写和处理</button></div>
        <p className="fg-kind-note">{kind === 'tool' ? '例如文字统计、数据计算。选择简单工具模板，写清楚输入什么、怎样处理、返回什么。' : '例如刚才的分享会准备。选择流程模板，写清楚要保存的事项、办理步骤、参与人员和操作界面。'}</p>
        <ol className="fg-install-steps"><li><strong>取得对应模板</strong><p>从项目的模块模板开始，已有程序也可以保留原来的处理逻辑。</p></li><li><strong>实现你的功能</strong><p>编写处理代码和操作界面，落实上面整理的规则。Python 和 Node.js 都有接入示例。</p></li><li><strong>安装并设置使用人员</strong><p>在工作台登记功能，安装操作界面，设置谁能使用。</p></li><li><strong>从头到尾试一次</strong><p>分别从页面和对话办理，检查结果、退回修改和刷新后的恢复。</p></li></ol>
        <a className="fg-text-button" href="/reports/handbooks/module-debug.html" target="_blank" rel="noreferrer">阅读详细开发说明<ArrowUpRight size={15}/></a>
      </div>
    </details>
    <div className="fg-ready"><div><ShieldCheck size={25}/><div><h2>已经准备好动手了吗？</h2><p>进入工作台后登录，在“步骤工作台”中打开示例，体验实际保存和办理。</p></div></div><a className="fg-button fg-primary" href="#flows">打开实际工作台<ArrowUpRight size={17}/></a></div>
  </>;
}

function readStoredGuide() {
  try { return restoreGuide(localStorage.getItem(STORAGE_KEY)); } catch { return initialGuide(); }
}

export default function FrameworkGuide() {
  const [state, dispatch] = useReducer(guideReducer, undefined, readStoredGuide);
  const [storageAvailable, setStorageAvailable] = useState(true);
  const main = useRef<HTMLElement>(null);
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); setStorageAvailable(true); }
    catch { setStorageAvailable(false); }
  }, [state]);
  function navigate(chapter: Chapter) {
    dispatch({ type: 'chapter', chapter });
    main.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  return <div className="framework-guide">
    <a className="fg-skip" href="#guide-content" onClick={event => { event.preventDefault(); main.current?.focus(); }}>跳到正文</a>
    <header className="fg-header"><a href="#guide" className="fg-brand" onClick={() => navigate(0)}><span className="fg-brand-symbol"><LayoutTemplate size={21}/><MessageCircle size={12}/></span><span>框架使用指南<small>一步一步，亲手试一试</small></span></a><div className="fg-header-links"><a className="fg-workspace-link" href="#developer-guide">框架全貌<ArrowUpRight size={16}/></a><a className="fg-workspace-link" href="#flows">打开工作台<ArrowUpRight size={16}/></a></div></header>
    <nav className="fg-chapters" aria-label="学习步骤">{CHAPTERS.map((label, index) => <button key={label} onClick={() => navigate(index as Chapter)} aria-current={state.chapter === index ? 'step' : undefined}><span>{index + 1}</span>{label}<ArrowRight size={15}/></button>)}</nav>
    {!storageAvailable && <p className="fg-storage-warning" role="status">浏览器暂时无法保存练习。你仍可以操作，关闭或刷新页面后会从头开始。</p>}
    <main id="guide-content" ref={main} tabIndex={-1} className="fg-main">
      {state.chapter === 0 ? <Introduction/> : state.chapter === 1 ? <Practice state={state} dispatch={dispatch} storageAvailable={storageAvailable}/> : state.chapter === 2 ? <Cooperation/> : <OwnFeature/>}
    </main>
    <footer className="fg-footer"><div>{state.chapter > 0 && <button className="fg-text-button" onClick={() => navigate((state.chapter - 1) as Chapter)}><ArrowLeft size={16}/>上一步</button>}</div><span>{state.chapter + 1} / {CHAPTERS.length}<span className="fg-footer-caption"> · {CHAPTERS[state.chapter]}</span></span>{state.chapter < 3 ? <button className="fg-button fg-primary" onClick={() => navigate((state.chapter + 1) as Chapter)}>{['开始动手体验', '看看框架怎样配合', '换成自己的功能'][state.chapter]}<ArrowRight size={17}/></button> : <button className="fg-button fg-secondary" onClick={() => navigate(1)}>回到动手练习<ArrowRight size={17}/></button>}</footer>
  </div>;
}
