"""Run the reviewed release checks with read-only source and no network/home."""
import argparse
import errno
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
API_TEST_FILES = (
    'test_hackathon_summary.py', 'test_config.py',
    'test_auth.py', 'test_database.py', 'test_database_startup.py',
    'test_guardrails.py', 'test_route_contract.py', 'test_observability.py',
    'test_redaction.py', 'test_reputation.py', 'test_risk_book.py',
    'test_phase6.py', 'test_phase4.py', 'test_phase3.py', 'test_api_tokens.py',
    'test_rate_limit.py', 'test_readiness_telemetry.py', 'test_agent_chat.py',
    'test_open_release_auth.py',
)
EXPECTED_CASES = 269
OMIT = {'.git', '.venv', 'node_modules', 'dist', '__pycache__', '.pytest_cache',
        '.mypy_cache', '.ruff_cache', 'playwright-report', 'test-results'}


def public_files(root):
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name not in OMIT]
        for name in files:
            path = Path(directory) / name
            if name == '.env' or (name.startswith('.env.') and name != '.env.example'):
                continue
            if path.is_symlink():
                raise ValueError('Symlinks are not admitted to the release snapshot')
            yield path


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in public_files(root)}


def dependency_manifest(root):
    """Hash package files without following dependency symlinks."""
    result = {}
    def visit(directory):
        for path in sorted(directory.iterdir()):
            key = str(path.relative_to(root))
            if path.is_symlink():
                result[key] = ['symlink', os.readlink(path)]
            elif path.is_dir():
                visit(path)
            elif path.is_file():
                result[key] = ['file', path.stat().st_mode & 0o777, hashlib.sha256(path.read_bytes()).hexdigest()]
            else:
                raise ValueError('Special dependency files are not admitted')
    visit(root)
    return result


def stage_dependencies(source, target):
    source = Path(source).resolve(strict=True)
    before = dependency_manifest(source)
    # Namespace setup may run as root on hosted Ubuntu, whose runner home is
    # private to another uid. Copy packages before entering that user namespace.
    shutil.copytree(source, target, symlinks=True, copy_function=shutil.copy)
    assert dependency_manifest(target) == before, 'Dependency staging changed content'
    return source, target, before


def probe():
    status = Path('/proc/self/status').read_text()
    assert int(next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')), 16) == 0
    assert next(line.split()[1] for line in status.splitlines() if line.startswith('NoNewPrivs:')) == '1'
    assert not Path('/home/martin').exists()
    assert not Path('/work/.git').exists()
    assert not Path('/work/.env').exists()
    assert os.environ['HOME'] == '/tmp'
    try:
        descriptor = os.open('/work/README.md', os.O_WRONLY)
    except OSError as error:
        assert error.errno == errno.EROFS
    else:
        os.close(descriptor)
        raise AssertionError('Source must be read-only')
    with socket.socket() as connection:
        connection.settimeout(.5)
        try:
            connection.connect(('1.1.1.1', 443))
        except OSError as error:
            assert error.errno == errno.ENETUNREACH
        else:
            raise AssertionError('Unexpected egress')


def inner(scope):
    probe()
    if scope == 'api':
        os.chdir('/work/services/api')
        subprocess.run(['/opt/venv/bin/python', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
            '--basetemp=/tmp/ttm-pytest', '--junitxml=/tmp/ttm-unit.xml',
            *['tests/' + name for name in API_TEST_FILES]], check=True)
        suites = ET.parse('/tmp/ttm-unit.xml').getroot().findall('.//testsuite')
        assert sum(int(suite.attrib['tests']) for suite in suites) == EXPECTED_CASES
        for key in ('errors', 'failures', 'skipped'):
            assert sum(int(suite.attrib[key]) for suite in suites) == 0
        print(f'Accepted exactly {EXPECTED_CASES} synthetic/mock unit cases; no integration run.')
    else:
        os.chdir('/work/apps/web-next')
        subprocess.run(['/opt/node', 'node_modules/typescript/bin/tsc', '--noEmit'], check=True)
        subprocess.run(['/opt/node', 'node_modules/vite/bin/vite.js', 'build',
            '--outDir', '/tmp/ttm-web-dist', '--emptyOutDir', '--configLoader', 'runner'], check=True)
    subprocess.run(['/usr/bin/python3', '/work/scripts/run-lab-tests.py', 'ToTheMoonTokens-open', scope], check=True, timeout=180, stdin=subprocess.DEVNULL)


def sandbox(snapshot, python_venv, node_modules, node):
    command = ['bwrap', '--unshare-all', '--unshare-user', '--disable-userns',
               '--die-with-parent', '--new-session', '--clearenv', '--cap-drop', 'ALL']
    for name in ('usr', 'bin', 'sbin', 'lib', 'lib64'):
        path = Path('/') / name
        if path.is_symlink():
            command += ['--symlink', os.readlink(path), '/' + name]
        elif path.exists():
            command += ['--ro-bind', str(path), str(path)]
    if Path('/etc/ld.so.cache').exists():
        command += ['--ro-bind', '/etc/ld.so.cache', '/etc/ld.so.cache']
    runtime = Path(sys.base_prefix).resolve()
    if runtime not in (Path('/usr'), Path('/usr/local')):
        if not str(runtime).startswith('/opt/hostedtoolcache/Python/'):
            raise ValueError('Unsupported Python runtime mount')
        command += ['--ro-bind', str(runtime), str(runtime),
                    '--setenv', 'LD_LIBRARY_PATH', str(runtime / 'lib')]
    command += ['--proc', '/proc', '--dev', '/dev', '--size', '536870912', '--tmpfs', '/tmp',
                '--ro-bind', str(snapshot), '/work']
    if python_venv:
        command += ['--ro-bind', str(Path(python_venv).resolve()), '/opt/venv']
    if node_modules:
        command += ['--ro-bind', str(Path(node_modules).resolve()), '/work/apps/web-next/node_modules']
    if node:
        command += ['--ro-bind', str(Path(node).resolve()), '/opt/node']
    for key, value in {'HOME': '/tmp', 'PATH': '/opt/venv/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                       'PYTHONPATH': '/work/services/api/src', 'PYTHONDONTWRITEBYTECODE': '1',
                       'PYTHONNOUSERSITE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                       'APP_ENV': 'local', 'CI': 'true', 'TMPDIR': '/tmp'}.items():
        command += ['--setenv', key, value]
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', choices=('api', 'web'))
    parser.add_argument('--python-venv')
    parser.add_argument('--node-modules')
    parser.add_argument('--node')
    parser.add_argument('--inner', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inner:
        inner(args.scope)
        return
    if args.scope == 'api' and not args.python_venv:
        parser.error('api checks require an explicit dependency-only Python venv')
    if args.scope == 'web' and not (args.node and args.node_modules):
        parser.error('web checks require explicit Node and dependency paths')
    before = hashes(ROOT)
    with tempfile.TemporaryDirectory(prefix='ttm-public-check-') as temporary:
        scratch = Path(temporary)
        snapshot = scratch / 'source'
        for source in public_files(ROOT):
            target = snapshot / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (snapshot / 'apps/web-next/node_modules').mkdir(parents=True, exist_ok=True)
        assert hashes(snapshot) == before
        dependency_sources = []
        python_venv, node_modules = args.python_venv, args.node_modules
        if python_venv:
            original, python_venv, manifest = stage_dependencies(python_venv, scratch / 'venv')
            dependency_sources.append((original, python_venv, manifest))
        if node_modules:
            original, node_modules, manifest = stage_dependencies(node_modules, scratch / 'node_modules')
            dependency_sources.append((original, node_modules, manifest))
        command = sandbox(snapshot, python_venv, node_modules, args.node)
        command += ['--chdir', '/work', '--',
                    '/opt/venv/bin/python' if args.python_venv else sys.executable,
                    '/work/scripts/checks-offline.py', args.scope, '--inner']
        result = subprocess.run(command, env={'PATH': '/usr/bin:/bin'}, timeout=600)
        for original, staged, manifest in dependency_sources:
            assert dependency_manifest(original) == manifest, 'Installed dependencies changed'
            assert dependency_manifest(staged) == manifest, 'Staged dependencies changed'
        assert hashes(snapshot) == before, 'Snapshot changed during isolated checks'
        assert hashes(ROOT) == before, 'Source changed during isolated checks'
        if result.returncode:
            raise SystemExit(result.returncode)
    print('Source hashes preserved; private home, credentials and network were unavailable.')


if __name__ == '__main__':
    main()
