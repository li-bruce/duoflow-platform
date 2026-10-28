// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import {cleanup,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import MonitoringSummary from './MonitoringSummary';
import type {MonitoringSnapshot} from './MonitoringSummary';
afterEach(cleanup);
const value:MonitoringSnapshot={mode:'independent',state:'connected',message:'已从独立监控服务取得汇总。',last_received_at:1800000000,interval_seconds:5,
  summary:{total:1,healthy:1,degraded:0,offline:0,stale:0,unknown:0},items:[{id:'demo-service',revision:2,
    load:{state:'fresh',last_received_at:1800000000,fresh_instances:1,reported_instances:1,
      metrics:{cpu_percent:10,rss_bytes:1048576,inflight_requests:0,queue_depth:null,requests_total:100,errors_total:0}}}]};
it('显示真实零与缺失值的区别，同时说明汇总来源',()=>{
  render(<MonitoringSummary value={value} serviceId="demo-service" revision={2}/>);
  const region=screen.getByRole('region',{name:'独立监控汇总'});
  expect(within(region).getByText('10.0%')).toBeVisible();expect(within(region).getByText('1.0 MiB')).toBeVisible();
  expect(within(region).getAllByText('0')).toHaveLength(2);
  expect(within(region).getByText('未知')).toBeVisible();expect(region.textContent).toContain('每 5 秒读取汇总');
});
it.each(['unavailable','stale'] as const)('监控%s不继续显示旧负载或伪造模块离线',state=>{
  render(<MonitoringSummary value={{...value,state}} serviceId="demo-service" revision={2}/>);
  expect(screen.queryByText('10.0%')).toBeNull();expect(screen.getAllByText('未知')).toHaveLength(6);
  expect(screen.queryByText(/健康 1/)).toBeNull();
});
it('连接修订变化时旧负载失效',()=>{
  render(<MonitoringSummary value={value} serviceId="demo-service" revision={3}/>);
  expect(screen.queryByText('10.0%')).toBeNull();expect(screen.getByText('尚无当前连接的负载报告。')).toBeVisible();
});
it('未取得配置读取结果时显示尚无汇总',()=>{
  render(<MonitoringSummary/>);expect(screen.getByText('尚无汇总')).toBeVisible();expect(screen.queryByText('未配置')).toBeNull();
});
