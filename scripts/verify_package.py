"""Audit and install the wheel outside the checkout, then render a no-API demo."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from zipfile import ZipFile


def audit_names(names: list[str]) -> None:
    forbidden = {'.env', '.learnmargin', '.venv', 'node_modules', 'test-results', 'artifacts', '__pycache__'}
    for name in names:
        parts = set(Path(name).parts)
        if parts & forbidden or name.lower().endswith(('.epub', '.pyc', '.pdf')):
            raise AssertionError(f'Unexpected private/runtime artifact in package: {name}')


def audit_wheel(wheel: Path) -> dict:
    with ZipFile(wheel) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        audit_names(names)
        assert 'learnmargin/web/index.html' in names
        assert any(name.startswith('learnmargin/web/assets/') and name.endswith('.js') for name in names)
        assert 'learnmargin/resources/skill/SKILL.md' in names
        chapters = set()
        for name in names:
            data = archive.read(name)
            if name.endswith(('book-foundations.md', 'book-practice-memory.md', 'book-mastery-exams.md')):
                chapters.update(int(value) for value in re.findall(r'(?m)^## 第(\d+)章', data.decode('utf-8')))
            if name.endswith(('.py', '.md', '.html', '.json', '.yaml', '.toml', '.js', '.css')):
                assert not re.search(rb'[A-Za-z]:[\\/]Users[\\/]|1779775642187|pred-2026-H', data), name
                assert not re.search(rb'sk-[A-Za-z0-9_-]{24,}', data), f'Possible API credential: {name}'
        assert chapters == set(range(1, 19)), chapters
        return {'wheel_files': len(names), 'skill_chapters': sorted(chapters),
                'frontend_assets': [name for name in names if name.startswith('learnmargin/web/')],
                'private_files_or_known_credential_patterns': []}


def request(base: str, path: str, *, post: bool = False) -> bytes:
    req = urllib.request.Request(base + path, data=b'' if post else None, method='POST' if post else 'GET')
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    wheel = args.wheel.resolve() if args.wheel else next(root.joinpath('dist').glob('learnmargin-*.whl'))
    report = audit_wheel(wheel)
    report.update(wheel=wheel.name, wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    source = wheel.parent / f'learnmargin-{wheel.name.split("-")[1]}.tar.gz'
    with tarfile.open(source) as archive:
        names = archive.getnames()
        audit_names(names)
        assert any(name.endswith('/frontend/dist/index.html') for name in names)
        report['sdist_files'] = len(names)
        report['sdist_includes_compiled_frontend'] = True
        report['sdist_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    skill = root / 'dist' / 'learnmargin-skill.zip'
    with ZipFile(skill) as archive:
        audit_names(archive.namelist())
        assert archive.testzip() is None
        report['skill_archive_files'] = len(archive.namelist())
        report['skill_archive_sha256'] = hashlib.sha256(skill.read_bytes()).hexdigest()

    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required to create the isolated package verification environment.')
    isolated = Path(tempfile.mkdtemp(prefix='learnmargin-package-')).resolve()
    assert not isolated.is_relative_to(root)
    environment = isolated / 'venv'
    subprocess.run([uv, 'venv', '--python', sys.executable, str(environment)], check=True, cwd=isolated)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([uv, 'pip', 'install', '--python', str(python), str(wheel)], check=True, cwd=isolated)
    installed = subprocess.check_output([
        str(python), '-I', '-c',
        'import json,learnmargin;from learnmargin.config import skill_directory;'
        'print(json.dumps({"module":learnmargin.__file__,"skill":str(skill_directory())}))',
    ], cwd=isolated, text=True)
    locations = json.loads(installed)
    assert Path(locations['module']).resolve().is_relative_to(environment)
    assert Path(locations['skill']).resolve().is_relative_to(environment)
    report['installed_locations'] = locations
    report['isolated_directory'] = str(isolated)

    clean_env = {key: value for key, value in os.environ.items()
                 if not key.startswith(('LEARNMARGIN_', 'DEEPSEEK_', 'OPENAI_'))
                 and 'API_KEY' not in key and key not in {'PYTHONPATH', 'PYTHONHOME'}}
    clean_env['LEARNMARGIN_DATA_DIR'] = str(isolated / 'data')
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0))
        port = bound.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    log = (isolated / 'server.log').open('w', encoding='utf-8')
    process = subprocess.Popen(
        [str(python), '-I', '-m', 'learnmargin.cli', '--port', str(port)], cwd=isolated,
        env=clean_env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
    )
    try:
        deadline = time.monotonic() + 40
        while True:
            try:
                report['health'] = json.loads(request(base, '/api/health'))
                break
            except (OSError, urllib.error.URLError):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f'Installed application did not start; see {isolated / "server.log"}')
                time.sleep(0.25)
        index = request(base, '/').decode('utf-8')
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', index)
        assert assets
        for asset in assets:
            assert request(base, asset)
        report['served_frontend_assets'] = assets
        settings = json.loads(request(base, '/api/settings'))
        assert settings['api']['has_api_key'] is False
        report['api_credentials_present'] = False
        job = json.loads(request(base, '/api/demo', post=True))
        deadline = time.monotonic() + 150
        while True:
            job = json.loads(request(base, f'/api/jobs/{job["id"]}'))
            if job['status'] == 'completed':
                break
            if job['status'] in {'failed', 'cancelled'} or time.monotonic() > deadline:
                raise RuntimeError(f'Installed demonstration did not complete: {job}')
            time.sleep(0.5)
        pdf = isolated / 'installed-demo.pdf'
        pdf.write_bytes(request(base, job['artifacts']['pdf']))
        assert pdf.read_bytes().startswith(b'%PDF-')
        result = subprocess.check_output([
            str(python), '-I', '-c',
            'import json,sys;from pypdf import PdfReader;r=PdfReader(sys.argv[1]);'
            'print(json.dumps({"pages":len(r.pages),"text_chars":[len(p.extract_text().strip()) for p in r.pages],'
            '"size":[float(r.pages[0].mediabox.width),float(r.pages[0].mediabox.height)]}))', str(pdf),
        ], cwd=isolated, text=True)
        report['installed_demo'] = json.loads(result)
        assert report['installed_demo']['pages'] >= 3
        assert all(count > 8 for count in report['installed_demo']['text_chars'])
        assert abs(report['installed_demo']['size'][0] - 595.28) < 1
        assert job['demo'] is True
        with ZipFile(io.BytesIO(request(base, job['artifacts']['source_zip']))) as archive:
            generation = json.loads(archive.read('generation.json'))
            assert generation['demo'] is True and generation['api_usage'] == []
            validation = json.loads(archive.read('validation.json'))
            assert validation['overflow'] == [] and validation['browser_errors'] == []
            report['installed_demo']['internal_links'] = validation['internal_links']
        report['paid_api_calls'] = 0
        report['limitations'] = ['Uses already installed Chromium and system fonts; the wheel does not bundle them.',
                                 'Archive checks cover unexpected paths and known credential patterns, not every possible secret format.']
        report['result'] = 'passed'
        output = root / 'artifacts' / 'package-verification.json'
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log.close()


if __name__ == '__main__':
    main()
