import test from 'node:test';
import assert from 'node:assert/strict';
import {createTelemetryReporter} from './telemetry.mjs';
const config={url:'http://127.0.0.1:8032/api/v1/monitor/reports/demo-service',token:'synthetic-telemetry-token-123456789',instanceId:'one'};
test('real process sample is bounded and sends directly to collector with no business data',async()=>{
  let sent;
  const reporter=createTelemetryReporter({...config, metrics:()=>({inflight_requests:0,requests_total:2}), fetchImpl:async(url,options)=>{
    sent={url:String(url),options,body:JSON.parse(options.body)};return new Response('{}');}});
  await reporter.report();await reporter.stop();
  assert.equal(sent.url,config.url);assert.equal(sent.options.redirect,'error');
  assert.equal(sent.options.headers.Authorization,'Bearer '+config.token);
  assert(sent.body.metrics.rss_bytes>0);assert(sent.body.metrics.cpu_percent>=0&&sent.body.metrics.cpu_percent<=100);
  assert.equal(sent.body.metrics.inflight_requests,0);assert(!('queue_depth' in sent.body.metrics));
  assert.equal(JSON.stringify(sent.body).includes(config.token),false);assert.equal(reporter.stats().sent,1);
});
test('unreachable collector and invalid counters are isolated and never retried immediately',async()=>{
  let calls=0;
  const reporter=createTelemetryReporter({...config,fetchImpl:async()=>{calls++;throw new Error(config.token);}});
  await reporter.report();assert.equal(calls,1);assert.equal(reporter.stats().failed,1);await reporter.stop();
  const invalid=createTelemetryReporter({...config,metrics:()=>({business_body:'SECRET'}),fetchImpl:async()=>{calls++;}});
  await invalid.report();assert.equal(calls,1);assert.equal(invalid.stats().failed,1);await invalid.stop();
});
test('only one report may be inflight',async()=>{
  let release;
  const reporter=createTelemetryReporter({...config,fetchImpl:()=>new Promise(resolve=>{release=resolve;})});
  const first=reporter.report();await reporter.report();assert.equal(reporter.stats().skipped,1);
  release(new Response('{}'));await first;await reporter.stop();
});
test('URL and identity settings reject external plain HTTP, query secrets and malformed values',()=>{
  for(const override of [{url:'http://example.org/api/v1/monitor/reports/demo-service'},
    {url:config.url+'?token=private'},{url:'https://user:secret@example.org/api/v1/monitor/reports/demo-service'},
    {instanceId:null},{intervalSeconds:0},{token:'short'}])
    assert.throws(()=>createTelemetryReporter({...config,...override}),/Invalid telemetry configuration/);
});
