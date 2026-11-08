"""Differential integration check; original full defaults on both sides."""
import ast
import io
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from common import PROJECT, catalog, sha256, linux_path, backend_command, timed_process
from diagnostics import atomic_json
from import_v5 import checked_members
from run_v5_case import execute, verify, resource_check, REPORTS


def main():
    manifest=verify();out=REPORTS/'equivalence';attempt=1
    while out.exists():
        attempt+=1;out=REPORTS/('equivalence_'+str(attempt))
    out.mkdir(parents=True,exist_ok=False)
    files=manifest['files']
    for name in files:
        if name.endswith('.py'):ast.parse((PROJECT/'v5/engine'/name).read_bytes())
    # Synthetic traversal and symlink checks never extract untrusted members.
    for name,mode in [('../escape.py',0),('/absolute.py',0),('a\\b.py',0),('link',0o120777)]:
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            entry=zipfile.ZipInfo(name);entry.filename=name;entry.external_attr=mode<<16;z.writestr(entry,'bad')
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as z:
            try:checked_members(z)
            except ValueError:pass
            else:raise AssertionError('Unsafe entry accepted: '+name)
    check=resource_check('toy','wsl')
    if not check['allowed']:
        atomic_json(out/'result.json',{'status':'BLOCKED_RESOURCE','resource_check':check});return
    inputs={k:Path(catalog()['toy'][k]) for k in ['def','lef','thermal']}
    hashes={k:sha256(p) for k,p in inputs.items()}
    direct=out/'direct';direct.mkdir();guide=direct/'route.guide'
    # Extraction lives outside engine and is removed on success or failure.
    with tempfile.TemporaryDirectory(prefix='v5_original_',dir=out) as temp:
        temp=Path(temp).resolve()
        assert temp.is_relative_to(out.resolve())
        with zipfile.ZipFile(PROJECT/'v5/original/eda260890.zip') as z:
            for info in checked_members(z):
                dest=temp/info.filename;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(info))
        assert all(sha256(temp/k)==h for k,h in files.items())
        args=['--def',linux_path(inputs['def']),'--lef',linux_path(inputs['lef']),
              '--thermal-report',linux_path(inputs['thermal']),'--output',linux_path(guide)]
        cmd=['python3',linux_path(PROJECT/'scripts/linux_supervisor.py'),'--directory',linux_path(direct),
             '--run-id',uuid.uuid4().hex,'--lease-seconds','120','--','bash',linux_path(temp/'run.sh'),*args]
        env={'PYTHONPATH':'','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
        command=backend_command(cmd,'wsl',cwd=temp,time_file=direct/'resource_usage.txt',env=env)
        execution=timed_process(command,direct/'router.log',float('inf'),monitor_dir=direct,stall_after=300)
        atomic_json(direct/'execution.json',dict(execution,command=command,input_sha256=hashes,environment=env,resource_check=check))
        assert execution['returncode']==0,'Direct original failed; retain evidence'
        assert all(sha256(temp/k)==h for k,h in files.items())
    integrated=execute('toy','toy_v5','wsl')
    assert integrated['status']=='ROUTED','Adapter failed'
    original=json.loads(guide.with_suffix('.run.json').read_text(encoding='utf8'))
    same=sha256(guide)==integrated['guide_sha256']
    result={'status':'PASS' if same else 'FAIL','original_guide_sha256':sha256(guide),
            'adapter_guide_sha256':integrated['guide_sha256'],'adapter_label':integrated['label'],
            'input_sha256':hashes,'same_inputs':hashes==integrated['input_sha256'],
            'original_validation':original['validation'],'adapter_validation':integrated['original_validation'],
            'same_validation':original['validation']==integrated['original_validation'],
            'default_parameters':not original['development_small_budget'] and integrated['original_default_parameters'],
            'python_environment':check['linux']['python'],'source_integrity':verify()['file_count'],
            'archive_security_tests':'PASS','original_python_ast':'PASS',
            'published_toy_hash_matches':sha256(guide)=='7faa81d5b62aa00c633a01b1219484ae375cca1fef42e71cf8e4a74f7396344c',
            'published_input_identity':'Not recorded in zip_verification.json; historical hash is descriptive, not an asserted test oracle'}
    atomic_json(out/'result.json',result)
    if not same or not result['same_inputs'] or not result['same_validation']:raise AssertionError('Differential mismatch; no engine changes permitted')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
