"""Read-only Router identity lookup. No compilation, registry writes or Git calls."""
import json,re
from pathlib import Path
from common import PROJECT

REGISTRY=PROJECT/'build/router_versions.json'
VERSION=re.compile(r'v[1-9][0-9]*\Z')
SHA256=re.compile(r'[0-9a-f]{64}\Z')
METHOD='independent_git_rebuild_sha256_and_cli_help'

def display_path(path):
    path=Path(path).resolve()
    try:return path.relative_to(PROJECT.resolve()).as_posix()
    except ValueError:return str(path)

def verified_record(version,record):
    """A manually set verified=true alone is not a registration receipt."""
    if not isinstance(version,str) or not VERSION.fullmatch(version) or not isinstance(record,dict):return False
    proof=record.get('verification',{})
    return (record.get('verified') is True and record.get('path')=='build/router_'+version
        and isinstance(record.get('sha256'),str) and bool(SHA256.fullmatch(record['sha256']))
        and isinstance(record.get('git_commit'),str) and bool(re.fullmatch(r'[0-9a-f]{40}',record['git_commit']))
        and isinstance(proof,dict) and proof.get('method')==METHOD
        and proof.get('rebuilt_sha256')==record['sha256']
        and isinstance(proof.get('source_sha256'),dict) and bool(proof['source_sha256'])
        and bool(record.get('compiler')) and bool(record.get('build_command'))
        and proof.get('cli_help_returncode')==0 and proof.get('cli_required_options_verified') is True)

def load_registry(path=None):
    with Path(path or REGISTRY).open(encoding='utf8') as f:data=json.load(f)
    if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(data.get('versions'),dict):
        raise ValueError('Unsupported Router version registry structure')
    if any(not isinstance(record,dict) for record in data['versions'].values()):
        raise ValueError('Router version entries must be objects')
    return data

def identify_router(digest,path=None):
    result={'router_version':'UNKNOWN','router_version_matches':[],'router_version_status':'UNREGISTERED'}
    if digest is None:
        return dict(result,router_version_status='BINARY_UNREADABLE',router_version_reason='Cannot read selected Router binary SHA256')
    try:data=load_registry(path)
    except FileNotFoundError:
        return dict(result,router_version_status='REGISTRY_MISSING',router_version_reason='Router version registry is missing; execution remains available')
    except (OSError,ValueError,UnicodeError,RecursionError) as e:
        return dict(result,router_version_status='REGISTRY_INVALID',router_version_reason='Cannot use Router version registry: '+str(e))
    matches=sorted((v for v,r in data['versions'].items() if verified_record(v,r) and r['sha256']==digest),key=lambda v:int(v[1:]))
    result['router_version_matches']=matches
    if len(matches)==1:
        return dict(result,router_version=matches[0],router_version_status='MATCHED',router_version_reason='Selected binary SHA256 matches a verified registration')
    if len(matches)>1:
        return dict(result,router_version_status='AMBIGUOUS',router_version_reason='Ambiguous SHA256: '+', '.join(matches))
    return dict(result,router_version_reason='Selected SHA256 has no verified version registration')
