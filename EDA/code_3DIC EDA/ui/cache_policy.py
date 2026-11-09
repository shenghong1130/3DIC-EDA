"""Bound disposable UI artifacts only. Never traverses or deletes source data."""
import re
import threading
from pathlib import Path

MAX_CACHE_BYTES=8*1024**3
MAX_CACHE_FILES=64
_lock=threading.Lock()
_artifact=re.compile(r'(?:[0-9a-f]{24}\.sqlite|v5_guide_[0-9a-f]{24}\.json|thermal_[0-9a-f]{24}\.json)\Z')


def trim_cache(directory,keep=(),byte_limit=MAX_CACHE_BYTES,file_limit=MAX_CACHE_FILES):
    """Oldest derived artifacts expire; in-use Windows handles are skipped.

    keep contains sources/indexes of the publishing worker. An expired older
    client receives the existing source-changed/retry response, never stale data.
    """
    directory=Path(directory).resolve();keep={Path(p).resolve() for p in keep}
    with _lock:
        files=[]
        for p in directory.iterdir():
            if not _artifact.fullmatch(p.name) or p.is_symlink() or not p.is_file():continue
            if p.resolve().parent!=directory:continue
            stat=p.stat();files.append((stat.st_mtime_ns,stat.st_size,p))
        total=sum(size for _,size,_ in files);count=len(files)
        for _,size,p in sorted(files):
            if total<=byte_limit and count<=file_limit:break
            if p.resolve() in keep:continue
            try:p.unlink()
            except OSError:continue
            total-=size;count-=1
        if total>byte_limit or count>file_limit:raise ValueError('UI disposable cache quota reached; release old UI sessions and retry')
