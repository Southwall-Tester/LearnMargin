"""Exercise the real local app without calling any paid model API.

Usage: python -X utf8 frontend/e2e/smoke.py [--base-url http://127.0.0.1:8765]
Requires the Python project's installed dependencies and Chromium. Outputs are ignored.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import time
from pathlib import Path
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader


@contextmanager
def record_report(output: Path, report: dict):
    report['result'] = 'running'
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        yield
    except BaseException as error:
        report['result'] = 'failed'
        report['error'] = str(error)
        raise
    else:
        report['result'] = 'passed'
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--import-only', action='store_true', help='Check upload through the Vite proxy without rendering a second demo.')
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    output = base.parent / 'test-results' / ('proxy' if args.import_only else 'real-backend')
    output.mkdir(parents=True, exist_ok=True)
    report: dict = {'base_url': args.base_url, 'paid_generation_requests': 0, 'page_errors': [], 'checks': []}
    start = time.monotonic()

    with record_report(output, report), sync_playwright() as playwright:
        # Full Chromium in headless mode includes its native PDF viewer; the
        # default headless-shell executable intentionally does not.
        browser = playwright.chromium.launch(headless=True, channel='chromium')
        page = browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))

        def guard_paid_generation(route):
            if route.request.method == 'POST':
                report['paid_generation_requests'] += 1
                route.abort('blockedbyclient')
                raise AssertionError('The smoke test must not call POST /api/jobs.')
            route.continue_()

        page.route('**/api/jobs', guard_paid_generation)
        page.goto(args.base_url, wait_until='networkidle')
        expect(page.get_by_role('button', name='生成学习讲义', exact=True)).to_be_enabled()
        report['checks'].append('settings_loaded')
        page.locator('.settings-toggle').click()
        page.get_by_label('思考强度', exact=True).select_option('low')
        page.get_by_label('请求超时秒数').fill('600')
        page.get_by_role('button', name='保存模型偏好').click()
        page.reload(wait_until='networkidle')
        page.locator('.settings-toggle').click()
        expect(page.get_by_label('思考强度', exact=True)).to_have_value('low')
        expect(page.get_by_label('请求超时秒数')).to_have_value('600')
        for width in (1440, 390):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.locator('#model-settings').screenshot(path=str(output / f'model-settings-{width}.png'))
        report['checks'].append('reasoning_effort_and_timeout_persist_desktop_mobile')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.get_by_label('思考强度', exact=True).select_option('')
        page.get_by_role('button', name='收起模型设置').click()
        page.get_by_label('选择学习材料文件').set_input_files([str(base / 'fixtures' / 'probability.md'), str(base / 'fixtures' / 'notes.txt')])
        expect(page.get_by_label('将 probability.md 纳入学习范围')).to_be_checked(timeout=45000)
        expect(page.get_by_label('将 notes.txt 纳入学习范围')).to_be_checked(timeout=45000)
        expect(page.locator('.source-preview pre')).to_contain_text('条件概率与方向', timeout=15000)
        report['checks'].append('two_real_files_imported_and_previewed')
        tips = page.locator('.import-notes')
        expect(tips.locator('summary')).to_have_text('导入提示（1）')
        expect(tips).not_to_have_attribute('open', '')
        tips.locator('summary').click()
        expect(tips.locator('li')).to_have_count(1)
        expect(tips).to_contain_text('probability.md、notes.txt')
        tips.locator('summary').click()
        report['checks'].append('nonblocking_import_notes_collapsed_and_deduplicated')

        page.get_by_role('button', name='指定页码 / 章节', exact=True).click()
        expect(page.get_by_label('probability.md 的材料角色')).to_have_value('primary')
        expect(page.get_by_label('notes.txt 的材料角色')).to_have_value('reference')
        expect(page.get_by_label('notes.txt 的范围')).to_have_count(0)
        expect(page.get_by_label('probability.md 的范围')).to_have_value('1-1')
        page.get_by_label('probability.md 的范围').fill('1')
        page.get_by_label('notes.txt 的材料角色').select_option('primary')
        expect(page.get_by_label('notes.txt 的范围')).to_have_value('1-1')
        page.get_by_label('notes.txt 的材料角色').select_option('reference')
        expect(page.get_by_label('notes.txt 的范围')).to_have_count(0)
        report['checks'].append('primary_range_and_automatic_reference_roles')
        page.get_by_role('button', name='指定知识点', exact=True).click()
        page.get_by_label('想弄明白的知识点').fill('条件概率的分母与独立性判断')
        expect(page.get_by_label('想弄明白的知识点')).to_have_value('条件概率的分母与独立性判断')
        report['checks'].append('range_and_topic_controls')

        expect(page.get_by_label('PDF 版式')).to_have_value('a4')
        page.get_by_label('PDF 版式').select_option('wide')
        expect(page.get_by_label('PDF 版式')).to_have_value('wide')
        page.get_by_label('PDF 版式').select_option('a4')
        page.get_by_role('button', name='看答案会，换题不会', exact=True).click()
        expect(page.get_by_role('button', name='看答案会，换题不会', exact=True)).to_have_attribute('aria-pressed', 'true')
        report['checks'].append('a4_default_and_wide_switch')
        page.screenshot(path=str(output / 'materials.png'), full_page=True)

        if not args.import_only:
            with page.expect_response(lambda response: response.url.endswith('/api/demo') and response.request.method == 'POST') as response_info:
                page.get_by_role('button', name='体验内置示例', exact=False).click()
            response = response_info.value
            assert response.ok, f'Demo start failed: {response.status} {response.text()}'
            job_id = response.json()['id']
            report['demo_job_id'] = job_id
            expect(page.get_by_text('内置示例 · 未调用模型', exact=True)).to_be_visible(timeout=10000)
            download_pdf = page.get_by_role('link', name='下载 PDF', exact=True)
            # Observe the real job so a renderer error fails immediately with
            # its actual reason instead of timing out on a missing button.
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                result = page.request.get(f'{args.base_url}/api/jobs/{job_id}').json()
                assert result['status'] not in ['failed', 'cancelled'], result.get('error') or result['status']
                if result['status'] == 'completed':
                    break
                page.wait_for_timeout(500)
            else:
                raise AssertionError('The bundled demonstration did not finish in 180 seconds.')
            expect(download_pdf).to_be_visible(timeout=15000)
            page.get_by_role('button', name='PDF 预览', exact=True).click()
            with page.expect_download() as download_info:
                download_pdf.click()
            pdf_path = output / 'demo.pdf'
            download_info.value.save_as(pdf_path)
            pdf = PdfReader(pdf_path)
            report['pdf_pages'] = len(pdf.pages)
            report['pdf_first_page_size'] = [round(float(pdf.pages[0].mediabox.width), 2), round(float(pdf.pages[0].mediabox.height), 2)]
            assert len(pdf.pages) >= 3
            assert all(page.extract_text().strip() for page in pdf.pages)
            with page.expect_download() as download_info:
                page.get_by_role('link', name='可编辑源文件', exact=True).click()
            zip_path = output / 'editable-sources.zip'
            download_info.value.save_as(zip_path)
            with ZipFile(zip_path) as archive:
                assert archive.testzip() is None
                report['source_files'] = archive.namelist()
                assert any(name.endswith('lesson.json') for name in archive.namelist())
                assert any(name.endswith('lesson.html') for name in archive.namelist())
            expect(page.locator('.pdf-frame')).to_have_attribute('src', f'/api/jobs/{job_id}/artifacts/lesson.pdf#toolbar=0&navpanes=0&view=FitH')
            report['native_pdf_viewer_active'] = any(frame.url.startswith('chrome-extension://') for frame in page.frames)
            assert report['native_pdf_viewer_active'], 'Chromium native PDF viewer did not load.'
            expect(page.locator('.history-item.current')).to_be_visible()
            report['checks'].append('real_demo_completed_pdf_and_sources_downloaded_history_visible')
            page.get_by_role('tab', name='材料预览', exact=True).click()
            expect(page.locator('.source-preview pre')).to_contain_text('条件概率与方向')
            page.get_by_role('tab', name='生成的讲义', exact=True).click()
            expect(download_pdf).to_be_visible()
            page.screenshot(path=str(output / 'completed-desktop.png'), full_page=True)

        page.get_by_role('button', name='指定页码 / 章节', exact=True).click()
        for width in [390, 768]:
            page.set_viewport_size({'width': width, 'height': 844})
            assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth'), f'Horizontal overflow at {width}px'
            if not args.import_only:
                page.locator('#result-panel').scroll_into_view_if_needed()
                expect(page.get_by_role('link', name='下载 PDF', exact=True)).to_be_visible()
                # Chromium's native PDF plugin repaints asynchronously after a
                # viewport resize; give its compositor time before capture.
                page.wait_for_timeout(750)
            page.screenshot(path=str(output / f'completed-{width}.png'), full_page=True)
        report['checks'].append('390px_and_768px_no_horizontal_overflow')
        assert not report['page_errors'], report['page_errors']
        assert report['paid_generation_requests'] == 0
        report['elapsed_seconds'] = round(time.monotonic() - start, 2)
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
