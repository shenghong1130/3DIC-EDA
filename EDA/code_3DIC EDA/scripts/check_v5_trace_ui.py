"""Bounded read-only API/render fixtures from a completed local Trace run."""
import argparse
import json
import sys
import time
import threading
from pathlib import Path
from urllib.request import urlopen
from common import PROJECT,catalog
sys.path.insert(0,str(PROJECT/'ui'))
import app
from v5_trace_store import V5TraceStore


def main():
    p=argparse.ArgumentParser();p.add_argument('--label',default='toy_v5_trace');a=p.parse_args()
    out=PROJECT/'outputs/reports/v5_trace';out.mkdir(parents=True,exist_ok=True)
    s=V5TraceStore(PROJECT/'outputs/runs/toy'/a.label,app.CACHE,catalog()['toy']['reference'])
    deadline=time.monotonic()+60
    while True:
        state=s.status()
        if state['state']=='ready':break
        if state['state']=='error' or time.monotonic()>deadline:raise RuntimeError(state)
        time.sleep(.05)
    with s.trace_db() as c:
        verified=[r[0] for r in c.execute("SELECT net FROM final WHERE provenance='FINAL_ACCEPTED_VERIFIED'")]
        lost=[r[0] for r in c.execute("SELECT net FROM final WHERE provenance='PATH_PROVENANCE_LOST'")]
    assert verified
    samples=[s.net(name) for name in verified]
    net=max(samples,key=lambda n:(bool(n.get('trace_tsv_edges')),len(n['edges'])))
    assert net['edges'] and net['nodes'] and net['path_provenance']=='FINAL_ACCEPTED_VERIFIED'
    if lost:
        unknown=s.net(lost[0]);assert not unknown['edges'];assert unknown['guides']
    else:unknown=None
    fixture={'summary':s.summary(),'net':net,'lost':unknown,'label':a.label}
    (out/'ui_fixture.json').write_text(json.dumps(fixture,ensure_ascii=False),encoding='utf8')
    class Quiet(app.Handler):
        def log_message(self,*args):pass
    server=app.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def get(path):
        with urlopen('http://127.0.0.1:'+str(server.server_port)+path) as f:return json.load(f)
    try:
        from urllib.parse import urlencode
        q=urlencode({'id':'toy/'+a.label,'net':net['net']})
        actual=get('/api/net?'+q);assert actual['edges']==net['edges']
        trace=get('/api/trace?'+q+'&limit=20');assert len(trace['events'])<=2080
        assert get('/api/trace?'+urlencode({'id':'toy/'+a.label}))['requires_net']
        reference=s.summary(True);assert reference['source']=='official_reference_guide'
        assert not reference['capabilities']['route_edges']
        result={'status':'PASS','label':a.label,'selected_net':net['net'],'verified_nets':len(verified),'lost_nets':len(lost),'edges':len(net['edges']),'vias':net['via_count'],'tsvs':net['tsv_count'],'http_net':True,'http_paged_trace':True,'reference_unchanged':True}
    finally:server.shutdown();server.server_close();thread.join()
    (out/'ui_api_result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
