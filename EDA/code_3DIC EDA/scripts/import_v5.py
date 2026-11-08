"""Import the checksum-pinned original engine without rewriting any member."""
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]
OUTER_SHA = '89b4cab808deab71664e5e1c7012bf23d0eeb16f28e047defb98ab63b1d8b266'
INNER_SHA = 'b29871dd42cc3fa8269f2e9bd5e2bf9597786ce7e7cdb3555e647c2e4e6c651f'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_members(archive):
    seen = set()
    for info in archive.infolist():
        name = info.orig_filename
        if '\x00' in name:
            raise ValueError('NUL in ZIP member')
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith('/') or '\\' in name or ':' in name
                or '..' in parts or any(p.endswith((' ', '.')) for p in parts)
                or stat.S_ISLNK(info.external_attr >> 16)
                or any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts)):
            raise ValueError('Unsafe ZIP member: '+name)
        key = name.rstrip('/').casefold()
        if key in seen:
            raise ValueError('Duplicate ZIP member: '+name)
        seen.add(key)
        if info.flag_bits & 1 or info.file_size > 64*1024*1024:
            raise ValueError('Unsupported ZIP member: '+name)
    if sum(i.file_size for i in archive.infolist()) > 256*1024*1024:
        raise ValueError('Archive expansion limit')
    if archive.testzip() is not None:
        raise ValueError('CRC failed')
    return [i for i in archive.infolist() if not i.is_dir()]


def import_engine(path):
    data = Path(path).read_bytes()
    if digest(data) != OUTER_SHA:
        raise ValueError('Outer ZIP SHA256 mismatch; import stopped')
    with zipfile.ZipFile(io.BytesIO(data)) as outer:
        checked_members(outer)
        prefix = '20261106_v16_safe/'
        selected = outer.read(prefix+'eda260890.zip')
        if digest(selected) != INNER_SHA:
            raise ValueError('Selected ZIP SHA256 mismatch; import stopped')
        evidence = {name: outer.read(prefix+name) for name in [
            'final_choice.json', 'eda260890.manifest.json', 'code_manifest.json',
            'zip_verification.json', 'code_zip_verification.json']}
        choice = json.loads(evidence['final_choice.json'])
        if choice['selected_code_zip_sha256'] != INNER_SHA:
            raise ValueError('Final choice conflicts with selected package')
        with zipfile.ZipFile(io.BytesIO(selected)) as inner:
            members = checked_members(inner)
            if len(members) != 79:
                raise ValueError('Expected exactly 79 original files')
            files = {i.filename: inner.read(i) for i in members}
        hashes = {name: digest(raw) for name, raw in files.items()}
        checks = {}
        for name in ['eda260890.manifest.json', 'code_manifest.json']:
            manifest = json.loads(evidence[name]); listed = manifest['files']
            if any(hashes.get(k) != h for k, h in listed.items()):
                raise ValueError('Source manifest mismatch: '+name)
            if manifest.get('zip_sha256', INNER_SHA) != INNER_SHA:
                raise ValueError('ZIP identity mismatch in '+name)
            checks[name] = {'matched_files': len(listed), 'unlisted_files': sorted(set(files)-set(listed))}
        if json.loads(evidence['zip_verification.json'])['zip_sha256'] != INNER_SHA:
            raise ValueError('Verification identity mismatch')
        preflight = outer.read(prefix+'code_preflight.zip')
        if digest(preflight) != json.loads(evidence['code_zip_verification.json'])['zip_sha256']:
            raise ValueError('Historical preflight identity mismatch')
    target = PROJECT/'v5'
    target.mkdir(exist_ok=False)
    (target/'original').mkdir(); (target/'engine').mkdir()
    (target/'original/eda260890.zip').write_bytes(selected)
    for name, raw in evidence.items():
        (target/'original'/name).write_bytes(raw)
    for name, raw in files.items():
        dest = target/'engine'/name
        if not dest.resolve().is_relative_to((target/'engine').resolve()):
            raise ValueError('Destination escaped engine')
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as f:
            f.write(raw)
    record = {'project_version': 'v5', 'engine_type': 'PYTHON_SCRIPT_ENGINE',
              'original_version': '0.16-final-input-only', 'outer_sha256': OUTER_SHA,
              'selected_zip_sha256': INNER_SHA, 'files': hashes, 'file_count': len(files),
              'manifest_checks': checks, 'historical_claim_only': choice,
              'internal_budget_seconds': 39600, 'source_modified': False}
    text = json.dumps(record, ensure_ascii=False, indent=2)+'\n'
    (target/'original/source_manifest.json').write_text(text, encoding='utf8')
    report = PROJECT/'outputs/reports/v5';report.mkdir(parents=True, exist_ok=True)
    with (report/'v5_source_integrity.json').open('x', encoding='utf8') as f:
        f.write(text)
    assert all(digest((target/'engine'/name).read_bytes()) == h for name, h in hashes.items())
    print(json.dumps({'status': 'IMPORTED_BYTE_IDENTICAL', 'files': len(files), 'manifest_checks': checks}, ensure_ascii=False))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip', type=Path)
    import_engine(parser.parse_args().zip)
