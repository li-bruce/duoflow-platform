import importlib.util
from pathlib import Path

import httpx
import pytest

spec=importlib.util.spec_from_file_location('portable_telemetry',Path(__file__).parents[1]/'telemetry.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
CONFIG={'url':'http://127.0.0.1:8032/api/v1/monitor/reports/demo-service','token':'synthetic-telemetry-token-123456789','instance_id':'one'}


def test_real_process_sample_and_no_business_payload_or_secret_in_body():
    requests=[]
    transport=httpx.MockTransport(lambda request:(requests.append(request) or httpx.Response(200,json={})))
    reporter=module.TelemetryReporter(**CONFIG,metrics=lambda:{'requests_total':2,'inflight_requests':0},transport=transport)
    reporter.report();reporter.stop()
    import json
    body=json.loads(requests[0].content)
    assert body['metrics']['rss_bytes']>0 and 0<=body['metrics']['cpu_percent']<=100
    assert body['metrics']['inflight_requests']==0 and 'queue_depth' not in body['metrics']
    assert CONFIG['token'].encode() not in requests[0].content and reporter.sent==1


def test_error_isolation_no_automatic_retry_and_counter_whitelist():
    calls=[]
    def fail(request):
        calls.append(request);raise httpx.ReadTimeout('PRIVATE')
    reporter=module.TelemetryReporter(**CONFIG,transport=httpx.MockTransport(fail))
    reporter.report();assert reporter.failed==1 and len(calls)==1
    reporter.metrics=lambda:{'private':'SECRET'};reporter.report();reporter.stop()
    assert reporter.failed==2 and len(calls)==1


@pytest.mark.parametrize('change',[{'interval_seconds':True},{'interval_seconds':float('nan')},
    {'token':'short'},{'url':CONFIG['url']+'?key=SECRET'},{'url':'http://example.org/api/v1/monitor/reports/demo-service'}])
def test_invalid_config(change):
    with pytest.raises(ValueError):module.TelemetryReporter(**{**CONFIG,**change})
