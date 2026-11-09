"""Read existing runs through HTTP; build UI caches only. No Router or scorer."""
import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'ui'))
import app


def main():
    if sys.platform=='win32':
        import ctypes
        kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000)
    class Quiet(app.Handler):
        def log_message(self,*args):pass
    server=app.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def get(endpoint,**query):
        start=time.monotonic()
        while True:
            with urlopen('http://127.0.0.1:'+str(server.server_port)+'/api/'+endpoint+'?'+urlencode(query),timeout=60) as r:
                data=json.load(r);status=r.status
            if status!=202:return data
            if time.monotonic()-start>1200:raise TimeoutError(data)
            time.sleep(.3)
    report={};fixture={}
    try:
        for case,label in [('open_3d_case001','case001_v5'),('open_3d_case001','case001_v4_5'),('open_3d_case003','case003_v5'),('open_3d_case005','case005_v5')]:
            key=case+'/'+label;started=time.monotonic()
            record=app.load(app.selected(key)/'run.json')
            if record['status']!='ROUTED' or record.get('returncode',0)!=0:
                report[key]={'status':'UNAVAILABLE_EXISTING_RUN','run_status':record['status'],'returncode':record.get('returncode'),'reason':'Existing run is incomplete; no Router is rerun and no intermediate Guide is represented as final.'}
                print(json.dumps({key:report[key]}),flush=True);continue
            s=get('summary',id=key)
            search=get('nets',id=key,cross='1',limit=1);n=get('net',id=key,net=search['items'][0]['net'])
            overview=get('geometry',id=key,full='1',box=','.join(map(str,s['die'])))
            assert len(s['nets'])==0 and len(overview['nets'])<=150
            assert len(json.dumps(overview,ensure_ascii=False).encode())<4194304
            pins=get('pins',id=key,box=','.join(map(str,s['die'])))
            assert len(pins['items'])<=12500
            page=get('pins',id=key,box=','.join(map(str,s['die'])),detail='1')
            assert len(page['items'])<=20
            g=n['guides'][0];cell=get('cell',id=key,gx=0,gy=0)
            if label.endswith('_v5'):
                assert n['edges']==[] and n['nodes']==[] and n['via_count'] is None
                assert n['estimated_paths']['segments'] and cell['resources']==[]
                assert 'coverage' in cell and 'unavailable_reason' in cell
                assert not get('trace',id=key)['available']
                assert all(p['box'] and p['terminal'] and p['access_point'] is None for p in n['pins'])
                for e in n['estimated_paths']['segments']:
                    box=n['guides'][e['guide_index']]
                    assert e['layer']==box[4]
                    assert all(box[0]<=x<=box[2] and box[1]<=y<=box[3] for x,y in (e['a'],e['b']))
                if case=='open_3d_case001':
                    assert s['metrics']['nets']==3152 and s['metrics']['guide_tsv_count']==657
                    fixture.update(v5_summary=s,v5_net=n,v5_overview=overview)
            else:
                assert n['edges'],'V4.5 actual Route must remain available'
                fixture.update(legacy_summary=s,legacy_net=n)
            clipped=get('geometry',id=key,full='1',box=','.join(map(str,g[:4])),layer=g[4])
            if label.endswith('_v5'):
                assert all(r[4]==g[4] and r[2]>=g[0] and r[0]<=g[2] and r[3]>=g[1] and r[1]<=g[3] for net in clipped['nets'] for r in net['guides'])
            report[key]={'status':'PASS','nets':s['metrics']['nets'],'rects':s.get('pin_rect_count'),'terminals':s.get('pin_terminal_count'),'overview_nets':len(overview['nets']),'overview_bytes':len(json.dumps(overview,ensure_ascii=False).encode()),'overview_truncated':overview.get('truncated'),'pin_display_items':len(pins['items']),'pin_viewport_rects':pins['total'],'pin_detail_page':len(page['items']),'selected_net':n['net'],'selected_guides':len(n['guides']),'selected_real_edges':len(n['edges']),'selected_estimates':len(n.get('estimated_paths',{}).get('segments',[])),'seconds_including_index':round(time.monotonic()-started,2)}
            print(json.dumps({key:report[key]}),flush=True)
        out=PROJECT/'outputs/reports/v5'
        (out/'v5_ui_existing_data_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        (out/'v5_ui_render_fixture.json').write_text(json.dumps(fixture,ensure_ascii=False),encoding='utf8')
        print('PASS: available existing V5 cases + V4.5 API / source separation; incomplete runs explicitly reported',flush=True)
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
