"""Evaluator evidence interpretation. Log text is for humans, never OOM proof."""
from pathlib import Path
import csv,datetime,io,json,os,re,subprocess


def log_tail(path,limit=32768):
    """Read a bounded tail; WSL can interleave UTF-16LE diagnostics with UTF-8."""
    try:
        with Path(path).open('rb') as f:
            f.seek(0,2);f.seek(max(0,f.tell()-limit));data=f.read(limit)
    except OSError:return ''
    return decode_mixed(data)


def decode_mixed(data):
    # Decode UTF-16LE runs independently, preserving subsequent UTF-8 bytes.
    pattern=rb'(?:[\x09\x0a\x0d\x20-\x7e]\x00){4,}'
    parts=[];pos=0
    for match in re.finditer(pattern,data):
        if match.start()<pos:continue
        parts.append(data[pos:match.start()].decode('utf8',errors='replace'))
        end=data.find(b'\n\x00',match.start())
        # A line boundary permits non-ASCII UTF-16 text after its ASCII prefix.
        end=end+2 if end>=0 and (end-match.start())%2==0 else match.end()
        parts.append(data[match.start():end].decode('utf-16le',errors='replace'));pos=end
    parts.append(data[pos:].decode('utf8',errors='replace'))
    return ''.join(parts).lstrip('\ufeff')


def read_report(path):
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict):return {},'INVALID: report root is not an object'
        if data.get('status') not in ('PASS','FAIL'):return data,'INVALID: missing/unknown official status'
        return data,'PRESENT'
    except FileNotFoundError:return {},'MISSING'
    except (OSError,ValueError) as e:return {},'INVALID: '+str(e)


def resource_tail(path,limit=65536):
    """Recover flushed samples after VM loss without requiring an exit record."""
    try:
        with Path(path).open('rb') as f:
            header=f.readline(4096);f.seek(0,2);size=f.tell()
            offset=max(len(header),size-limit);f.seek(offset);data=f.read(limit)
        if offset>len(header):data=data.partition(b'\n')[2]
        if not data.endswith(b'\n'):data=data.rpartition(b'\n')[0]+b'\n'
        rows=list(csv.DictReader(io.StringIO((header+data).decode('utf8',errors='replace'))))
        rows=[row for row in rows if str(row.get('pid','')).isdigit() and row.get('source')=='proc_evaluator_pid']
        peak=max((int(row['process_peak_rss_bytes']) for row in rows if str(row.get('process_peak_rss_bytes','')).isdigit()),default=None)
        return {'last_sample':rows[-1] if rows else {},'observed_peak_rss_bytes':peak,'source':'bounded CSV tail; sampled lower bound, not final peak'}
    except (OSError,ValueError,csv.Error):return {}


def classify_evaluator(result,supervisor,run_id,report,report_state,backend,after=None):
    trusted=supervisor if supervisor.get('run_id')==run_id else {}
    rc=trusted.get('evaluator_returncode');sig=trusted.get('evaluator_signal')
    evidence=[{'source':'host_wait','process_status':result.get('process_status'),
               'returncode':result.get('returncode'),'layer':result.get('returncode_layer','wsl.exe' if backend=='wsl' else 'wrapper/supervisor')}]
    if trusted:evidence.append({'source':'evaluator_process.json' if trusted.get('state')=='RUNNING' else 'evaluator_exit.json','returncode':rc,'signal':sig,
                                'termination_reason':trusted.get('termination_reason')})
    if result.get('process_status')=='TIMEOUT':return 'TIMEOUT',evidence
    if result.get('process_status')=='INTERRUPTED':return 'INTERRUPTED',evidence
    if result.get('process_status')=='LAUNCH_ERROR' or trusted.get('launch_error'):return 'LAUNCH_ERROR',evidence
    if trusted.get('oom_confirmed_for_pid'):
        evidence.append({'source':'kernel','records':trusted.get('kernel_oom_evidence')});return 'OOM_CONFIRMED',evidence
    reason=trusted.get('termination_reason')
    if reason=='time_budget':return 'TIMEOUT',evidence
    if reason=='wrapper_signal' and result.get('returncode')==124 and trusted.get('termination_requested_signal')==15:
        return 'TIMEOUT',evidence
    if reason in ('host_cancel','host_lease_expired','supervisor_cleanup'):
        return ('SUPERVISOR_ERROR' if trusted.get('supervisor_error') else 'INTERRUPTED'),evidence
    if sig:
        if sig==9 and (trusted.get('resource_summary',{}).get('cgroup_oom_kill_delta') or 0)>0:
            evidence.append({'source':'shared_cgroup','note':'oom_kill increased; victim PID not attributed'})
            return 'OOM_SUSPECTED',evidence
        return 'EVALUATOR_SIGNAL',evidence
    if trusted.get('supervisor_error'):return 'SUPERVISOR_ERROR',evidence
    if rc not in (None,0):
        return ('OFFICIAL_FAIL' if report_state=='PRESENT' and report.get('status')=='FAIL' else 'EVALUATOR_EXIT_ERROR'),evidence
    if backend=='wsl' and trusted.get('boot_id') and (after or {}).get('boot_id') and trusted['boot_id']!=after['boot_id']:
        evidence.append({'source':'boot_id','before':trusted['boot_id'],'after':after['boot_id'],
                         'note':'A different Linux boot was observed; cause of restart is unknown'})
        return 'WSL_RESTART_OBSERVED',evidence
    raw=result.get('returncode')
    if backend=='wsl' and (rc==0 and raw not in (0,None) or isinstance(raw,int) and (raw&0xffffffff)>=0x80000000):
        return 'WSL_EXIT_ERROR',evidence
    if not trusted or rc is None:return 'UNKNOWN',evidence
    if report_state=='MISSING':return 'REPORT_MISSING',evidence
    if report_state!='PRESENT':return 'REPORT_INVALID',evidence
    if raw!=0:return 'WRAPPER_EXIT_ERROR',evidence
    return ('SUCCESS' if report.get('status')=='PASS' else 'OFFICIAL_FAIL'),evidence


def wsl_snapshot():
    """One bounded post-failure probe, not periodic WSL launches. May start a new boot."""
    script="""import json,pathlib
r={}
for k,p in [('boot_id','/proc/sys/kernel/random/boot_id'),('uptime','/proc/uptime'),('mounts','/proc/mounts')]:
 try:r[k]=pathlib.Path(p).read_text()[:16384].strip()
 except OSError as e:r[k+'_error']=str(e)
print(json.dumps(r))
"""
    try:
        proc=subprocess.run(['wsl','-d','Ubuntu','--','python3','-'],input=script.encode(),capture_output=True,timeout=5)
        result={'probe_returncode':proc.returncode,'note':'Post-exit probe can start WSL; different boot_id proves a different boot, not why it stopped'}
        try:result.update(json.loads(proc.stdout))
        except (ValueError,UnicodeError):result['stdout']=decode_mixed(proc.stdout[-4096:])
        result['stderr']=decode_mixed(proc.stderr[-4096:])
        return result
    except (OSError,subprocess.TimeoutExpired) as e:return {'probe_error':str(e)}


def windows_events(started_at):
    """Bounded host evidence; NIC events are correlations, never crash/OOM proof."""
    if os.name!='nt':return {'available':False,'reason':'not Windows'}
    start=datetime.datetime.fromisoformat(started_at).astimezone(datetime.timezone.utc).isoformat()
    script="""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
try {
 Get-WinEvent -FilterHashtable @{ProviderName='Microsoft-Windows-Hyper-V-VmSwitch';StartTime=[datetime]'%s'} -MaxEvents 20 |
 Select-Object @{n='time';e={$_.TimeCreated.ToUniversalTime().ToString('o')}},Id,LevelDisplayName,@{n='message';e={$_.Message.Substring(0,[Math]::Min(1500,$_.Message.Length))}} | ConvertTo-Json -Compress
} catch { @{error=$_.Exception.Message} | ConvertTo-Json -Compress }
"""%start
    try:
        p=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=5)
        text=p.stdout.decode('utf8',errors='replace').strip()
        try:events=json.loads(text)
        except ValueError:events=text[-8192:]
        return {'returncode':p.returncode,'events':events,'note':'Correlation only; not evaluator victim attribution'}
    except (OSError,subprocess.TimeoutExpired) as e:return {'probe_error':str(e)}
