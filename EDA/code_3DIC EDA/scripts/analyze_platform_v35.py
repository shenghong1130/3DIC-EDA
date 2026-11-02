"""Archive and stream-audit supplied platform artifacts; never run a scorer."""
import collections
import csv
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

from common import PROJECT, catalog, sha256


def save(path, data):
    with path.open('x', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def extract(z, root):
    seen = set()
    if sum(i.file_size for i in z.infolist()) > 3 * 1024**3:
        raise ValueError('Archive exceeds explicit 3GiB extraction budget')
    for i in z.infolist():
        p = PurePosixPath(i.filename.replace('\\', '/'))
        if p.is_absolute() or '..' in p.parts or ':' in i.filename or ((i.external_attr >> 16) & 0o170000) == 0o120000:
            raise ValueError('Unsafe archive member: ' + i.filename)
        target = root.joinpath(*p.parts).resolve()
        if not target.is_relative_to(root.resolve()) or str(target).lower() in seen:
            raise ValueError('Archive path escape/collision')
        seen.add(str(target).lower())
        if i.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)  # EOF verifies CRC.


def main():
    base = PROJECT.parent / '\u8865\u5145\u8d44\u6599'
    archive = PROJECT / 'outputs/reports/official_platform/v3_5_24699'
    report = PROJECT / 'outputs/reports/v3_6'
    archive.mkdir(parents=True, exist_ok=False)
    report.mkdir(parents=True, exist_ok=False)
    inventory = [{'path': str(p), 'bytes': p.stat().st_size, 'sha256': sha256(p)}
                 for p in sorted(base.rglob('*')) if p.is_file()]
    save(archive / 'materials_inventory.json', inventory)
    protected = {}
    for rel in ['build/router_v3_5', 'configs/v3_5.json', 'reference_scorer/main.cpp',
                'include/physical_resources.hpp', 'include/model.hpp', 'include/json.hpp', 'src/parser.cpp']:
        protected[str(PROJECT/rel)] = sha256(PROJECT/rel)
    for case in ['open_3d_case001', 'open_3d_case003', 'open_3d_case005']:
        for key in ['def', 'lef', 'thermal']:
            p = Path(catalog()[case][key]); protected[str(p)] = sha256(p)
        for p in (PROJECT/'outputs/runs'/case/(case.replace('open_3d_', '')+'_v3_5')).rglob('*'):
            if p.is_file(): protected[str(p)] = sha256(p)
    protected[str(PROJECT.parent/'\u8bc4\u4f30\u7a0b\u5e8f/score')] = sha256(PROJECT.parent/'\u8bc4\u4f30\u7a0b\u5e8f/score')
    save(report/'protected_before.json', protected)
    save(report/'v35_registry_before.json', json.loads((PROJECT/'docs/router_versions.json').read_text(encoding='utf8'))['candidates']['v3_5'])
    zip_checks = []
    # All supplied ZIPs get CRC-checked by actual safe streaming extraction.
    # Old compatibility executables are data only; none is executed.
    for row in inventory:
        p = Path(row['path'])
        if p.suffix.lower() != '.zip': continue
        with tempfile.TemporaryDirectory(prefix='platform_material_') as temp, zipfile.ZipFile(p) as z:
            extract(z, Path(temp))
            zip_checks.append({'path': str(p), 'sha256': row['sha256'], 'members': len(z.infolist()), 'crc_verified': True})
        print('ZIP validated:', p.name, flush=True)
    save(archive/'zip_integrity.json', zip_checks)
    cases = {}; comparisons = []
    with tempfile.TemporaryDirectory(prefix='platform_baseline_') as temp:
        temp = Path(temp)
        with zipfile.ZipFile(base/'scoring_result.zip') as z: extract(z, temp/'scoring')
        with zipfile.ZipFile(base/'prediction_result.zip') as z: extract(z, temp/'prediction')
        raw = temp/'scoring'; prediction = temp/'prediction'
        scores = json.loads((raw/'scores.json').read_text(encoding='utf8'))
        save(archive/'scores.json', scores)
        save(archive/'ingestion_result.json', json.loads((prediction/'ingestion_result.json').read_text(encoding='utf8')))
        for filename in ['scoring_result.zip', 'prediction_result.zip']:
            with (base/filename).open('rb') as src, (archive/filename).open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
        with (report/'tsv_distribution_analysis.csv').open('x', encoding='utf8', newline='') as f, \
                (archive/'overflow_by_layer.csv').open('x', encoding='utf8', newline='') as lf:
            writer = csv.writer(f); writer.writerow(['case','col','row','tsv_count','nets','m10_overflow','m11_overflow'])
            layers_writer = csv.writer(lf); layers_writer.writerow(['case','layer','overflow','hotspots','demand','capacity'])
            for case in sorted(scores['cases']):
                d = raw/'eval_temp'/case
                r = json.loads((d/'evaluation_report.json').read_text(encoding='utf8'))
                save(archive/(case+'_evaluation_report.json'), r)
                local = PROJECT/'outputs/runs'/case/(case.replace('open_3d_', '')+'_v3_5')/'route.guide'
                platform_guide = prediction/(case+'.output.guide')
                same = sha256(local) == sha256(platform_guide)
                if not same: raise ValueError('Platform Guide differs: '+case)
                points = collections.defaultdict(list)
                with (d/'tsv_points.csv').open(encoding='utf-8-sig', newline='') as fp:
                    for p in csv.DictReader(fp): points[int(p['gcell_col']),int(p['gcell_row'])].append(p['net'])
                layer = collections.defaultdict(collections.Counter); hot = []; colocated_over = 0
                m10_11 = {}
                with (d/'overflow_gcells.csv').open(encoding='utf-8-sig', newline='') as fp:
                    for p in csv.DictReader(fp):
                        x,y = int(p['gcell_col']),int(p['gcell_row']); l = p['layer']; o = int(p['overflow'])
                        layer[l].update(overflow=o,hotspots=1,demand=int(p['demand']),capacity=int(p['capacity']))
                        hot.append(p) if len(hot)<20 else None  # file order is checked below, no assumed sorting
                        if (x,y) in points: colocated_over += o
                        if l in ['metal10','metal11'] and (x,y) in points:m10_11[x,y,l] = o
                # Exact top-20 in a second bounded heap pass, independent of CSV ordering.
                import heapq
                heap=[]
                with (d/'overflow_gcells.csv').open(encoding='utf-8-sig',newline='') as fp:
                    for serial,p in enumerate(csv.DictReader(fp)):
                        item=(int(p['overflow']),serial,p)
                        if len(heap)<20:heapq.heappush(heap,item)
                        elif item[:2]>heap[0][:2]:heapq.heapreplace(heap,item)
                hot=[p for _,_,p in sorted(heap,reverse=True)]
                for (x,y),nets in sorted(points.items()):
                    writer.writerow([case,x,y,len(nets),'|'.join(nets),m10_11.get((x,y,'metal10'),0),m10_11.get((x,y,'metal11'),0)])
                for l,vals in sorted(layer.items(),key=lambda kv:int(kv[0][5:])):
                    layers_writer.writerow([case,l,vals['overflow'],vals['hotspots'],vals['demand'],vals['capacity']])
                total = sum(p['overflow'] for p in layer.values())
                if total != r['congestion']['total_overflow']:raise ValueError('Overflow CSV/report mismatch '+case)
                if r != scores['cases'][case]['raw_report']:comparisons.append({'case':case,'difference':'evaluation_report vs scores.raw_report'})
                ref = None
                for root in ['outputs/reports/reference_20261029','outputs/reports/scoring/reference_20261029']:
                    p=PROJECT/root/case/(case.replace('open_3d_', '')+'_v3_5')/'reference_report.json'
                    if p.exists():ref=json.loads(p.read_text(encoding='utf8'));break
                cases[case]={'report':r,'scores':scores['cases'][case], 'guide_sha256':sha256(local),'guide_matches_platform':same,
                    'platform_executable_sha256':None,'platform_input_sha256':None,
                    'identity_limitation':'Provided platform metadata has no scorer/input hashes; submission id 24699 supplied by user',
                    'local_input_sha256':{k:sha256(catalog()[case][k]) for k in ['def','lef','thermal']},
                    'tsv_count':sum(map(len,points.values())),'unique_tsv_gcells':len(points),
                    'colocated_gcells':sum(len(v)>1 for v in points.values()),'max_tsv_per_cell':max(map(len,points.values())),
                    'overflow_by_layer':dict(layer),'overflow_at_tsv_xy':colocated_over,'top_hotspots':hot,
                    'reference_fixed_baseline_overflow':ref.get('fixed_baseline_overflow') if ref else None,
                    'official_fixed_vs_routed_decomposition':None,
                    'decomposition_limitation':'Platform CSV exposes total demand only. Spatial TSV coincidence is not causal attribution; reference fixed baseline has a different model.'}
                print(case,'overflow',total,'colocation',cases[case]['colocated_gcells'],'max',cases[case]['max_tsv_per_cell'],flush=True)
        # Preserve the raw archives, per-case reports and computed evidence; no
        # need to duplicate dozens of MB of dense heat matrices outside the ZIP.
        save(archive/'baseline.json',{'submission_id_user_supplied':24699,'scores':scores,'cases':cases,'raw_report_differences':comparisons})
    assert scores['total_score']==45.39
    expected={'open_3d_case001':[981,29,739], 'open_3d_case003':[971,80760,89501], 'open_3d_case005':[175416,55092,215144]}
    for case,row in cases.items():
        got=[row['overflow_by_layer'].get('metal'+str(l),{}).get('overflow',0) for l in [1,10,11]]
        if got!=expected[case]: comparisons.append({'case':case,'expected_M1_M10_M11':expected[case],'actual':got})
    save(report/'platform_validation.json',{'differences':comparisons,'guide_hashes_bound':True,'baseline_path':str(archive/'baseline.json')})


if __name__ == '__main__':main()
