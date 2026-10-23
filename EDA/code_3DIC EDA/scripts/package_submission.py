#!/usr/bin/env python3
from pathlib import Path
import argparse,zipfile,json,stat,hashlib
from common import PROJECT,OUTPUTS,save
def main():
    p=argparse.ArgumentParser(description='Create source submission ZIP; official qualification is reported separately.')
    p.add_argument('--output',type=Path,default=OUTPUTS/'submission_v1.zip');p.add_argument('--with-linux-binary',action='store_true');a=p.parse_args()
    target=a.output.resolve()
    if not target.is_relative_to(OUTPUTS.resolve()):p.error('Archive must stay inside outputs/')
    required=['build.sh','run.sh','CMakeLists.txt','README.md','docs/编译与运行说明.md','docs/设计架构.md','docs/算法原理.md','docs/第三方库说明.md','docs/优化结果对比.md']
    files=[PROJECT/x for x in required]+sorted((PROJECT/'src').rglob('*.cpp'))+sorted((PROJECT/'include').rglob('*.hpp'))
    # Tests belong to the development tree; submission CMake explicitly disables BUILD_TESTING.
    if a.with_linux_binary:
        binary=PROJECT/'router'
        if binary.read_bytes()[:4]!=b'\x7fELF':p.error('router is not a Linux ELF binary')
        files.append(binary)
    for f in files:
        if not f.is_file():p.error('Missing required submission file: '+str(f))
    target.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            rel=f.relative_to(PROJECT).as_posix();info=zipfile.ZipInfo(rel);info.create_system=3;info.external_attr=((stat.S_IFREG|0o755 if rel.endswith('.sh') or rel=='router' else stat.S_IFREG|0o644)<<16)
            data=f.read_bytes()
            if rel=='CMakeLists.txt':data=data.replace(b'include(CTest)',b'option(BUILD_TESTING "Build tests" OFF)\ninclude(CTest)')
            z.writestr(info,data,compress_type=zipfile.ZIP_DEFLATED)
    runs=[]
    for f in (OUTPUTS/'runs').glob('*/*/run.json'):
        d=json.loads(f.read_text(encoding='utf8'));runs.append({'case':d['case'],'label':d['label'],'official_status':d['official_status']})
    save(target.with_suffix('.manifest.json'),{'archive':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'files':[f.relative_to(PROJECT).as_posix() for f in files],'validation':runs,'qualification':'Local cases only; full official submission validation is not established.'})
    print(target)
if __name__=='__main__':main()
