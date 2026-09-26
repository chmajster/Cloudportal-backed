from abc import ABC, abstractmethod
import os
import base64
import re
import selectors
import signal
import subprocess
import time
from urllib.parse import quote


class ExecutionFailed(RuntimeError):
    pass


class Cancelled(ExecutionFailed):
    pass


class Executor(ABC):
    @abstractmethod
    def execute(self, operation, context):
        raise NotImplementedError


def redact(text, secrets):
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    variants = set()
    for value in secrets:
        if value:
            variants.update([value, quote(value, safe=''), base64.b64encode(value.encode()).decode(), *value.splitlines()])
    for secret in sorted(variants, key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[REDACTED]').replace(quote(secret, safe=''), '[REDACTED]')
    text = re.sub(r'(?i)((?:password|secret|token|authorization|private_key)\s*[=:]\s*)[^\s,]+', r'\1[REDACTED]', text)
    return text


def run_process(argv, cwd, env, context, secrets=()):
    """Fixed argument arrays, isolated environment, bounded output and process-group cancellation."""
    context.check()
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True, shell=False, umask=0o077)
    executable = os.path.basename(str(argv[0]))
    if executable in {'terraform', 'tofu'}:
        # Each Terraform/OpenTofu invocation owns an independent OS session and
        # process group. Cancellation targets only this process tree.
        context.log(f'{executable}.process.started: pid={process.pid} process_group={process.pid}')
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = b''
    log_bytes = 0
    discard = False
    try:
        while selector.get_map():
            context.check()
            for key, _ in selector.select(timeout=0.5):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if not discard and log_bytes < 1024 * 1024:
                        safe = redact(line.decode('utf-8', errors='replace'), secrets)
                        context.log(safe[:8192])
                        log_bytes += len(safe)
                    discard = False
                if len(pending) > 65536:
                    pending = b''
                    discard = True  # Never expose fragments of overlong secret-bearing lines.
        if pending and not discard and log_bytes < 1024 * 1024:
            context.log(redact(pending.decode('utf-8', errors='replace'), secrets)[:8192])
        code = process.wait(timeout=5)
        if executable in {'terraform', 'tofu'}:
            context.log(f'{executable}.process.finished: pid={process.pid} code={code}')
        if code != 0:
            raise ExecutionFailed(f'{os.path.basename(argv[0])} exited with code {code}')
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        process.stdout.close()


def execution_environment(workspace):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(workspace), 'LANG': 'C.UTF-8',
            'TF_IN_AUTOMATION': '1', 'TF_INPUT': '0', 'CHECKPOINT_DISABLE': '1'}
