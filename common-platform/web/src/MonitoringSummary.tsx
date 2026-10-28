import './MonitoringSummary.css';

export type MonitoringSnapshot = {
  mode:'independent';state:'connected'|'unconfigured'|'unavailable'|'stale';
  message:string;last_received_at:number|null;interval_seconds:number;
  summary:{total:number;healthy:number;degraded:number;offline:number;stale:number;unknown:number}|null;
  items:{id:string;revision:number;load:{state:'fresh'|'stale'|'unknown';last_received_at:number|null;
    fresh_instances:number;reported_instances:number;metrics:{cpu_percent:number|null;rss_bytes:number|null;
      inflight_requests:number|null;queue_depth:number|null;requests_total:number|null;errors_total:number|null}}}[];
};

export default function MonitoringSummary({value,serviceId,revision}:{value?:MonitoringSnapshot;serviceId?:string;revision?:number}){
  const connected=value?.state==='connected';
  const selected=connected?value.items.find(item=>item.id===serviceId&&item.revision===revision):undefined;
  const fresh=selected?.load.state==='fresh';
  const metrics=fresh?selected.load.metrics:undefined;
  const metric=(number:number|null|undefined,unit='')=>number===null||number===undefined?'未知':number.toLocaleString('zh-CN')+unit;
  const cpu=metrics?.cpu_percent;
  return <section className="monitoring-summary" aria-label="独立监控汇总">
    <div className="monitoring-heading"><h3>独立监控服务</h3><span className={'tag '+(connected?'green':'amber')}>{value?{connected:'已连接',unconfigured:'未配置',unavailable:'连接不可用',stale:'汇总已过期'}[value.state]:'尚无汇总'}</span></div>
    <p>{value?.message||'尚无独立监控汇总，请检查服务连接。'}</p>
    {connected&&value.summary&&<p className="monitoring-counts">共 {value.summary.total} 个服务 · 健康 {value.summary.healthy} · 降级 {value.summary.degraded} · 离线 {value.summary.offline} · 过期 {value.summary.stale} · 未知 {value.summary.unknown}</p>}
    <p className="subtle">主框架每 {value?.interval_seconds||5} 秒读取汇总；健康探测与负载报告由独立服务处理。{value?.last_received_at?` 最近取得汇总：${new Date(value.last_received_at*1000).toLocaleTimeString('zh-CN')}`:''}</p>
    {serviceId&&<><h4>当前服务负载</h4><p>{fresh?`${selected.load.fresh_instances} 个新鲜实例参与汇总` : selected?.load.state==='stale'?'实例报告已过期，当前负载未知。':'尚无当前连接的负载报告。'}</p>
      <dl className="monitoring-metrics">
        <div><dt>进程 CPU 均值</dt><dd>{cpu===null||cpu===undefined?'未知':cpu.toFixed(1)+'%'}</dd></div>
        <div><dt>进程内存合计</dt><dd>{metrics?.rss_bytes===null||metrics?.rss_bytes===undefined?'未知':(metrics.rss_bytes/1024/1024).toFixed(1)+' MiB'}</dd></div>
        <div><dt>请求并发</dt><dd>{metric(metrics?.inflight_requests)}</dd></div>
        <div><dt>队列积压</dt><dd>{metric(metrics?.queue_depth)}</dd></div>
        <div><dt>进程累计请求</dt><dd>{metric(metrics?.requests_total)}</dd></div>
        <div><dt>进程累计错误</dt><dd>{metric(metrics?.errors_total)}</dd></div>
      </dl><p className="subtle">缺失指标保持未知；进程计数不代表跨重启的历史总量。监控状态不代替业务审核结果。</p></>}
  </section>;
}
