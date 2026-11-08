"""External byte-integrity gate. Does not import or modify engine modules."""
import hashlib
import json
import zipfile
from pathlib import Path


def verify():
    base = Path(__file__).resolve().parent
    manifest = json.loads((base/'original/source_manifest.json').read_text(encoding='utf8'))
    expected = manifest['files']
    pinned_zip = 'b29871dd42cc3fa8269f2e9bd5e2bf9597786ce7e7cdb3555e647c2e4e6c651f'
    if manifest['selected_zip_sha256'] != pinned_zip:
        raise ValueError('V5 selected package pin changed')
    actual = {p.relative_to(base/'engine').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (base/'engine').rglob('*') if p.is_file()}
    if actual != expected:
        raise ValueError('V5 source integrity failed: '+str(sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))))
    if hashlib.sha256((base/'original/eda260890.zip').read_bytes()).hexdigest() != pinned_zip:
        raise ValueError('Original package integrity failed')
    with zipfile.ZipFile(base/'original/eda260890.zip') as archive:
        archived = {i.filename: hashlib.sha256(archive.read(i)).hexdigest() for i in archive.infolist() if not i.is_dir()}
    if len(archived) != 79 or archived != expected:
        raise ValueError('Source manifest differs from pinned original ZIP')
    return manifest


if __name__ == '__main__':
    verify()
