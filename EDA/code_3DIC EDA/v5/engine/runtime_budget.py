"""Bounded subprocess groups for the Linux final-evaluation environment."""
import os,signal,subprocess


def run_bounded(command,log,env,seconds):
    process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=os.name=='posix')
    try:return process.wait(timeout=max(.01,seconds))
    except subprocess.TimeoutExpired:
        if os.name=='posix':
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
        else:process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name=='posix':
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            else:process.kill()
            process.wait()
        raise
