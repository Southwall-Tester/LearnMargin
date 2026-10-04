"""Render authored local handout HTML and check planned pagination."""
import argparse
import asyncio
import json
from pathlib import Path
import fitz
from playwright.async_api import async_playwright

async def main(args):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            tab = await browser.new_page()
            await tab.route('http://**/*', lambda route: route.abort())
            await tab.route('https://**/*', lambda route: route.abort())
            await tab.goto(args.input.resolve().as_uri())
            await tab.emulate_media(media='print')
            await tab.evaluate('document.fonts.ready')
            planned = await tab.locator('section.page').count()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            await tab.pdf(path=str(args.output), print_background=True, prefer_css_page_size=True)
        finally:
            await browser.close()
    with fitz.open(args.output) as doc:
        report = {'output':str(args.output.resolve()), 'pages':len(doc), 'planned_pages':planned,
                  'text_characters':[len(page.get_text()) for page in doc]}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        if planned and planned != len(doc):
            raise ValueError(f'Unexpected pagination: planned {planned}, rendered {len(doc)}. Reflow the content.')
        if args.preview_dir:
            args.preview_dir.mkdir(parents=True, exist_ok=True)
            for i,page in enumerate(doc):
                page.get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(args.preview_dir/f'page-{i+1:02}.png')
    print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--preview-dir', type=Path)
    asyncio.run(main(parser.parse_args()))
