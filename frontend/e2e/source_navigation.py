"""Verify source round-trips against an existing real lesson, without model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8766')
    parser.add_argument('--job-id', required=True)
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'test-results' / 'source-navigation' / args.job_id
    output.mkdir(parents=True, exist_ok=True)
    report = {'job_id': args.job_id, 'base_url': args.base_url, 'source_round_trips': [], 'page_errors': []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel='chromium')
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        page.route('**/api/jobs', lambda route: route.abort('blockedbyclient') if route.request.method == 'POST' else route.continue_())
        job = page.request.get(f'{args.base_url}/api/jobs/{args.job_id}').json()
        assert job['status'] == 'completed', job
        all_jobs = page.request.get(f'{args.base_url}/api/jobs').json()
        job_index = next(i for i, item in enumerate(all_jobs) if item['id'] == args.job_id)
        page.goto(args.base_url, wait_until='networkidle')
        page.locator('.history-item').nth(job_index).click()
        page.get_by_role('button', name='网页阅读', exact=True).click()
        frame = page.frame_locator('.html-frame')
        expect(frame.locator('html')).to_have_class('embedded-reader', timeout=30000)
        refs = frame.locator('.web-source-button:visible').evaluate_all('(buttons) => buttons.map(button => button.dataset.sourceRef)')
        assert refs, 'The real lesson has no interactive source references.'
        selected_refs = list({ref.split(':')[0]: ref for ref in refs}.values())
        assert len(selected_refs) >= 2, 'The lesson should use both primary and reference materials.'
        for index, ref in enumerate(selected_refs):
            button = frame.locator(f'.web-source-button[data-source-ref="{ref}"]').first
            button.scroll_into_view_if_needed()
            before = button.evaluate('(button) => { window.__readerCheck = "same-dom"; return {scroll: window.scrollY, ref: button.dataset.sourceRef}; }')
            button.click()
            dialog = page.get_by_role('dialog')
            expect(dialog).to_be_visible()
            document_id, unit = ref.split(':')
            original = page.request.get(f'{args.base_url}/api/documents/{document_id}').json()
            expect(dialog.get_by_role('heading', name=original['name'], exact=True)).to_be_visible(timeout=15000)
            expect(dialog.locator('pre')).not_to_be_empty()
            page.screenshot(path=str(output / f'source-{index + 1}.png'))
            dialog.get_by_role('button', name='返回讲义', exact=True).last.click()
            expect(dialog).not_to_be_visible()
            expect(button).to_be_focused()
            after = button.evaluate('(button) => ({scroll: window.scrollY, marker: window.__readerCheck, focused: document.activeElement === button})')
            assert after['marker'] == 'same-dom'
            assert after['focused'], after
            assert abs(after['scroll'] - before['scroll']) <= 1, (before, after)
            report['source_round_trips'].append({'ref': ref, 'document': original['name'], 'scroll_before': before['scroll'], 'scroll_after': after['scroll'], 'focus_restored': True})

        # A vanished original must not replace or reload the lesson.
        unavailable_ref = selected_refs[0]
        unavailable_id = unavailable_ref.split(':')[0]
        page.route(f'**/api/documents/{unavailable_id}*', lambda route: route.fulfill(status=404, json={'detail': '材料不存在或已被移除。'}))
        source_button = frame.locator(f'.web-source-button[data-source-ref="{unavailable_ref}"]').first
        source_button.click()
        expect(page.get_by_role('dialog')).to_contain_text('原材料暂时无法打开，可能已被移除')
        page.keyboard.press('Escape')
        expect(page.get_by_role('dialog')).not_to_be_visible()
        expect(frame.locator('html')).to_have_class('embedded-reader')
        report['unavailable_source_handled'] = True
        page.unroute(f'**/api/documents/{unavailable_id}*')

        page.set_viewport_size({'width': 390, 'height': 844})
        source_button.click()
        expect(page.get_by_role('dialog')).to_be_visible()
        expect(page.get_by_role('dialog').locator('pre')).not_to_be_empty()
        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
        reader_dimensions = frame.locator('html').evaluate('''(html) => ({width: innerWidth, scroll: html.scrollWidth, overflow: [...document.querySelectorAll('*')].filter(node => node.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map(node => ({tag: node.tagName, className: node.className, width: node.getBoundingClientRect().width, right: node.getBoundingClientRect().right}))})''')
        assert reader_dimensions['scroll'] <= reader_dimensions['width'], reader_dimensions
        page.screenshot(path=str(output / 'mobile-source.png'))
        page.get_by_role('dialog').get_by_role('button', name='返回讲义', exact=True).last.click()
        expect(source_button).to_be_focused()
        page.locator('.preview-card').screenshot(path=str(output / 'mobile-reader.png'))
        report['mobile_no_horizontal_overflow'] = True
        assert not report['page_errors'], report['page_errors']
        report['result'] = 'passed'
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        browser.close()


if __name__ == '__main__':
    main()
