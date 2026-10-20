"""Additional public regressions; invoked only inside the reviewed namespace."""
import errno
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import subprocess
import sys
import time

PROJECT = 'ToTheMoonTokens-open'
MAX_FILES = 64
MAX_FILE_BYTES = 200_000
MAX_OUTPUT_BYTES = 2_000_000


def probe():
    if Path(__file__).resolve() != Path('/work/scripts/run-lab-tests.py'):
        raise ValueError('Additional regressions require the reviewed namespace')
    assert os.environ.get('HOME') == '/tmp'
    assert not Path('/home').exists() and not Path('/root').exists()
    assert not Path('/work/.git').exists() and not Path('/work/.env').exists()
    assert not any(name.endswith(('_KEY', '_SECRET', '_PASS')) for name in os.environ)
    status = Path('/proc/self/status').read_text()
    assert re.search(r'^CapEff:\s*0+$', status, re.M)
    assert re.search(r'^NoNewPrivs:\s*1$', status, re.M)
    mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    assert any(fields[4] == '/work' and 'ro' in fields[5].split(',') for fields in mounts)
    try:
        descriptor = os.open('/work/scripts/run-lab-tests.py', os.O_WRONLY)
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES)
    else:
        os.close(descriptor)
        raise AssertionError('Public source must be read-only')
    assert len(Path('/proc/net/route').read_text().strip().splitlines()) == 1
    with socket.socket() as connection:
        connection.settimeout(.5)
        try:
            connection.connect(('1.1.1.1', 443))
        except OSError as error:
            assert error.errno == errno.ENETUNREACH
        else:
            raise AssertionError('Unexpected external network')
    print('Lab namespace verified: source read-only, empty home, no external network, CapEff=0.', flush=True)


def run(command):
    process = subprocess.Popen(command, cwd='/work', stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + 120
    output = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('Additional public regressions timed out')
            if not selector.select(min(.2, remaining)):
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                break
            output.extend(data)
            if len(output) > MAX_OUTPUT_BYTES:
                raise ValueError('Additional regression output exceeds the reviewed limit')
        process.wait(timeout=max(.01, deadline-time.monotonic()))
        if output:
            print(output.decode('utf-8', errors='replace'), end='', flush=True)
        if process.returncode:
            raise ValueError('Additional public regressions failed')
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        raise
    finally:
        selector.close()
        process.stdout.close()


def main(project, mode):
    if project != PROJECT:
        raise ValueError('Unreviewed additional-regression project')
    modes = {'NexusOrchestrator-open': ('server', 'web'),
             'ToTheMoonTokens-open': ('api', 'web'),
             'Gest-o-de-Igrejas-open': ('all',), 'stone-open': ('all',)}
    if mode not in modes[project]:
        raise ValueError('Unreviewed additional-regression mode')
    probe()
    if project == 'NexusOrchestrator-open' and mode != 'web':
        return
    if project == 'ToTheMoonTokens-open' and mode != 'api':
        return
    root = Path('/work/lab_tests')
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Expected additional public regressions are missing')
    files = sorted(root.iterdir())
    if not files or len(files) > MAX_FILES:
        raise ValueError('Additional regression file count is outside the reviewed limit')
    for path in files:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Additional regressions must be bounded regular files')
    if project == 'ToTheMoonTokens-open':
        if any(not path.name.startswith('test_') or path.suffix != '.py' for path in files):
            raise ValueError('Unexpected Python regression file')
        run(['/opt/venv/bin/python', '-m', 'unittest', 'discover', '-s', str(root), '-v'])
    elif project == 'stone-open':
        if any(path.suffix != '.php' for path in files):
            raise ValueError('Unexpected PHP regression file')
        for path in files:
            run(['/opt/php', '-n', '-l', str(path)])
            run(['/opt/php', '-n', str(path)])
    else:
        if any(not path.name.endswith(('.test.mjs', '.test.js', '.test.ts')) for path in files):
            raise ValueError('Unexpected JavaScript regression file')
        loader = ('/work/web-v2/node_modules/tsx/dist/esm/index.mjs'
                  if project == 'NexusOrchestrator-open' else '/node_modules/tsx/dist/esm/index.mjs')
        run(['/opt/node/bin/node', '--import', loader, '--test', *map(str, files)])
    print('Additional public regressions passed; original suite/acceptance assertions were preserved.', flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Expected fixed project and reviewed scope')
    main(sys.argv[1], sys.argv[2])
