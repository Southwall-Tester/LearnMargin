"""Preserve an existing PDF at native size and add aligned learning sidebars.

Input notes are authored for the source: this renderer does not infer pedagogy.
Requires PyMuPDF and Playwright with an installed Chromium browser.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import tempfile
from pathlib import Path
from urllib.parse import quote

import fitz
from playwright.async_api import async_playwright



def e(value):
    return html.escape(str(value), quote=True)


def paragraphs(value, *, field):
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or any(not isinstance(text, str) for text in values):
        raise ValueError(f'{field} must be text or a list of text paragraphs')
    return [text.strip() for text in values if text.strip()]


def validate_cards(data):
    """Validate declared intent; do not guess intent from task vocabulary."""
    ids = set()
    questions = []
    for page in data['pages']:
        for card in page['cards']:
            card_id = card['id']
            if not isinstance(card_id, str) or not card_id.strip() or card_id in ids:
                raise ValueError(f'Card id must be nonempty and unique: {card_id!r}')
            ids.add(card_id)
            if 'requires_answer' in card:
                raise ValueError(f'{card_id}: replace requires_answer with kind=question/action and answer')
            kind = card.get('kind', 'action')  # Existing operation-only notes stay valid.
            if kind not in ('question', 'action'):
                raise ValueError(f'{card_id}: kind must be question or action')
            answer = paragraphs(card.get('answer'), field=f'{card_id}.answer')
            if kind == 'question' and not answer:
                raise ValueError(f'{card_id}: question requires a nonempty answer')
            if kind == 'action' and answer:
                raise ValueError(f'{card_id}: a card with an answer must use kind=question')
            if kind == 'question':
                questions.append({**card, 'answer': answer,
                                  'check': paragraphs(card.get('check'), field=f'{card_id}.check')})
    return questions


def make_html(source, data, width):
    sections = []
    sizes = []
    anchor_report = []
    for item in data['pages']:
        n = item['page']
        src_page = source[n - 1]
        height = src_page.rect.height
        sizes.append(f'@page p{n} {{size:{width}pt {height}pt; margin:0;}}')
        cards = []
        for card in item['cards']:
            if 'anchor_y_pt' in card:
                y = float(card['anchor_y_pt'])
                if not 0 <= y < height:
                    raise ValueError(f'Page {n}: anchor_y_pt outside page')
            else:
                matches = src_page.search_for(card['anchor'])
                if not matches:
                    raise ValueError(f"Page {n}: anchor not found: {card['anchor']}")
                y = matches[0].y0
            anchor_report.append({'page': n, 'id': card['id'], 'anchor_y': y})
            body = ''.join(f'<p>{e(p)}</p>' for p in card['body'])
            lines = ''.join('<div class="write-line"></div>' for _ in range(card.get('response_lines', 0)))
            if card.get('kind') == 'question':
                feedback = '<p><span class="answer-link">查看参考答案 →</span></p>'
            else:
                feedback = ''.join(f'<p>{e(p)}</p>' for p in paragraphs(card.get('check'), field='check'))
            cards.append(f'<article class="card" data-anchor="{y}" data-id="{e(card["id"])}"><div class="label"><b>{e(card["id"])}</b> {e(card["label"])}</div><h2>{e(card["title"])}</h2>{body}{lines}{feedback}</article>')
        pause = item.get('pause')
        if pause:
            minutes = pause['minutes']
            if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
                raise ValueError(f'Page {n}: pause minutes must be positive')
            footer_label = f'番茄休息点 · {minutes:g} 分钟'
            footer_body = ''.join(f'<p>{e(pause[key])}</p>' for key in ['when', 'activity', 'resume'])
            footer_class = 'pause'
        else:
            footer_label = '带着这一点继续'
            footer_body = e(item['footer'])
            footer_class = ''
        sections.append(f'''<section class="strip" style="page:p{n};height:{height}pt" data-page="{n}">
<header><span>边读边练</span><small>{e(data['version'])}</small></header>
<div class="goal"><div class="goal-label">这一页先抓住</div><p>{e(item['goal'])}</p></div>
{''.join(cards)}
<footer class="{footer_class}"><div class="foot-label">{e(footer_label)}</div>{footer_body}</footer>
<div class="folio">学习侧栏 · {n:02d}</div>
</section>''')
    css = '''
* {box-sizing:border-box}
html,body {margin:0;padding:0}
body {font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#294457;font-size:9.4pt;line-height:1.65}
.strip {position:relative;break-after:page;background:#f3f8fa;border-left:1pt solid #c5dce4;overflow:hidden}
.strip:last-child {break-after:auto}
header {position:absolute;left:15pt;right:15pt;top:38pt;border-bottom:1pt solid #bdd6df;padding-bottom:8pt;display:flex;align-items:center;justify-content:space-between;color:#145b70}
header span {font-size:14pt;font-weight:700;letter-spacing:1pt}
header small {font-size:8pt;color:#617c8d}
.goal {position:absolute;top:86pt;left:15pt;right:15pt}
.goal-label {color:#50788b;font-size:8.2pt;font-weight:bold;letter-spacing:.7pt}
p {margin:6pt 0 0}
.card {position:absolute;left:13pt;right:13pt;padding:11pt 11pt 10pt;background:white;border:0.6pt solid #d1e1e8;border-radius:5pt}
.label {font-size:7.8pt;color:#5c7c8a;line-height:1.45}
.label b {display:inline-block;background:#e5f2f4;color:#176477;padding:1pt 4pt;border-radius:2pt;margin-right:3pt;font-weight:600}
h2 {font-size:10.6pt;line-height:1.5;color:#174b62;margin:6pt 0 7pt}
.answer-link {display:inline-block;color:#145b70;border:0.6pt solid #bdd6df;border-radius:3pt;padding:3pt 5pt;font-weight:600}
.write-line {height:17pt;border-bottom:.6pt solid #dce6eb}
footer {position:absolute;left:15pt;right:15pt;bottom:49pt;border-top:.7pt solid #bfd7df;padding-top:9pt;font-size:8.2pt;line-height:1.65;color:#597383}
.foot-label {font-size:8pt;color:#27657a;font-weight:600;margin-bottom:4pt}
footer.pause {background:#fff5e6;border:0.7pt solid #e2bf83;border-radius:5pt;padding:10pt;color:#71532b}
footer.pause .foot-label {color:#85520c;font-size:9.5pt;font-weight:bold}
footer.pause p {margin:5pt 0 0}
.folio {position:absolute;bottom:21pt;left:15pt;right:15pt;text-align:center;font-size:7.2pt;color:#7b909d}
'''
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>'+css+''.join(sizes)+f'.strip {{width:{width}pt}}'+'</style><body>'+''.join(sections)+'</body></html>', anchor_report


async def render_answers(tab, questions, width, height, work):
    """Pack short answer cards into measured pages, with exact return targets."""
    if not questions:
        return []
    cards = []
    for card in questions:
        body = ''.join(f'<p>{e(text)}</p>' for text in card['answer'])
        check = ''.join(f'<p>{e(text)}</p>' for text in card['check'])
        cards.append(f'<article class="answer-card" data-id="{e(card["id"])}">'
                     f'<h2>{e(card["id"])} · {e(card["title"])}</h2>{body}'
                     f'<div class="check">{check}</div>'
                     '<p><span class="return-link">← 返回这条提示</span></p></article>')
    answer_html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>
* {box-sizing:border-box} html,body {margin:0;padding:0}
body {font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;color:#294457;font-size:10.5pt;line-height:1.7}
.answer-page {padding:42pt 45pt;break-after:page;overflow:hidden;display:flex;flex-direction:column}
.answer-page:last-child {break-after:auto}
h1 {font-size:17pt;color:#174b62;margin:0 0 20pt;flex-shrink:0}
.answer-content {flex:1;min-height:0;display:flow-root}
.answer-card {padding:0 0 15pt;margin:0 0 17pt;border-bottom:.6pt solid #d1e1e8;overflow-wrap:anywhere}
h2 {font-size:12pt;line-height:1.5;margin:0 0 8pt;color:#174b62}
p {margin:7pt 0 0;white-space:pre-wrap}
.check {color:#597383;font-size:9.5pt}
.return-link {display:inline-block;color:#145b70;border:.6pt solid #bdd6df;border-radius:3pt;padding:3pt 7pt;font-size:9.5pt}
''' + f'@page {{size:{width}pt {height}pt;margin:0}} .answer-page {{width:{width}pt;height:{height}pt}}' + \
        '</style><body><main></main><div id="pool">' + ''.join(cards) + '</div></body></html>'
    answer_path = work / 'answers.html'
    answer_path.write_text(answer_html, encoding='utf-8')
    await tab.goto(answer_path.as_uri())
    await tab.evaluate('document.fonts.ready')
    positions = await tab.evaluate('''() => {
const pt = 96 / 72;
const main = document.querySelector('main');
const cards = Array.from(document.querySelectorAll('.answer-card'));
let content;
function newPage() {
  const page = document.createElement('section'); page.className = 'answer-page';
  page.innerHTML = '<h1>参考答案</h1><div class="answer-content"></div>';
  main.append(page); content = page.querySelector('.answer-content');
}
newPage();
for (const card of cards) {
  content.append(card);
  if (content.scrollHeight > content.clientHeight + 1) {
    card.remove();
    if (!content.children.length) throw new Error('Answer ' + card.dataset.id + ' is too long for one page; shorten it or use a flowing layout.');
    newPage(); content.append(card);
    if (content.scrollHeight > content.clientHeight + 1) throw new Error('Answer ' + card.dataset.id + ' is too long for one page; shorten it or use a flowing layout.');
  }
}
document.querySelector('#pool').remove();
return Array.from(main.children).flatMap((page, index) => {
  const origin = page.getBoundingClientRect();
  return Array.from(page.querySelectorAll('.answer-card')).map(card => {
    const rect = card.getBoundingClientRect();
    const link = card.querySelector('.return-link').getBoundingClientRect();
    return {id:card.dataset.id, page:index + 1,
      to:[(rect.left-origin.left)/pt, (rect.top-origin.top)/pt],
      return_rect:[(link.left-origin.left)/pt, (link.top-origin.top)/pt,
                   (link.right-origin.left)/pt, (link.bottom-origin.top)/pt]};
  });
});
}''')
    await tab.pdf(path=str(work / 'answers.pdf'), print_background=True, prefer_css_page_size=True)
    return positions


async def build(args):
    source_path, out_path = args.source.resolve(), args.output.resolve()
    if source_path == out_path:
        raise ValueError('Use a different output filename to preserve the source PDF.')
    if not args.overview.is_file():
        raise FileNotFoundError('An authored opening overview HTML is required.')
    data = json.loads(args.notes.read_text(encoding='utf-8'))
    questions = validate_cards(data)
    source = fitz.open(source_path)
    if [p['page'] for p in data['pages']] != list(range(1, len(source)+1)):
        raise ValueError('Notes must match every source page, once, in page order.')
    width = float(data.get('sidebar_width_mm', 78)) * 72 / 25.4
    content, anchors = make_html(source, data, width)
    with tempfile.TemporaryDirectory(prefix='study_sidebars_') as tmp:
        work = Path(tmp)
        html_path = work/'sidebar.html'
        html_path.write_text(content, encoding='utf-8')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            tab = await browser.new_page()
            await tab.route('http://**/*', lambda route: route.abort())
            await tab.route('https://**/*', lambda route: route.abort())
            await tab.goto(html_path.as_uri())
            await tab.emulate_media(media='print')
            await tab.evaluate('document.fonts.ready')
            layout = await tab.evaluate('''() => {
const pt = 96 / 72;
return Array.from(document.querySelectorAll('.strip')).map(section => {
  const rect = section.getBoundingClientRect();
  const goal = section.querySelector('.goal').getBoundingClientRect();
  const footer = section.querySelector('footer').getBoundingClientRect();
  const cards = Array.from(section.querySelectorAll('.card'));
  const minimum = goal.bottom - rect.top + 15 * pt;
  const maximum = footer.top - rect.top - 15 * pt;
  const gap = 12 * pt;
  const heights = cards.map(c=>c.getBoundingClientRect().height);
  if (heights.reduce((a,b)=>a+b,0) + gap*(cards.length-1) > maximum-minimum) {
    throw new Error('Sidebar content too dense on page ' + section.dataset.page);
  }
  const tops = [];
  cards.forEach((c,i) => tops.push(Math.max(Number(c.dataset.anchor)*pt,
    i ? tops[i-1]+heights[i-1]+gap : minimum)));
  // Work upward when lower-page anchors would collide with the footer.
  for (let i=cards.length-1;i>=0;i--) {
    const bound = i===cards.length-1 ? maximum : tops[i+1]-gap;
    tops[i]=Math.min(tops[i],bound-heights[i]);
  }
  if (tops[0] < minimum-0.5) throw new Error('Overlapping goal on page '+section.dataset.page);
  cards.forEach((c,i)=>c.style.top=(tops[i]/pt)+'pt');
  return {page:Number(section.dataset.page),goalBottom:(minimum-15*pt)/pt,
    footerTop:(maximum+15*pt)/pt,cards:cards.map((c,i)=>{
      const box = c.getBoundingClientRect();
      const link = c.querySelector('.answer-link')?.getBoundingClientRect();
      return {id:c.dataset.id, left:(box.left-rect.left)/pt,
        top:tops[i]/pt,height:heights[i]/pt,anchor:Number(c.dataset.anchor),
        answer_rect:link ? [(link.left-rect.left)/pt,(link.top-rect.top)/pt,
                           (link.right-rect.left)/pt,(link.bottom-rect.top)/pt] : null};
    })};
});
}''')
            await tab.pdf(path=str(work/'sidebars.pdf'),print_background=True,prefer_css_page_size=True)
            if args.overview and args.overview.exists():
                await tab.goto(args.overview.resolve().as_uri())
                await tab.evaluate('document.fonts.ready')
                overview_bounds = await tab.evaluate('''() => {
const sheet = document.querySelector('.sheet').getBoundingClientRect();
const last = document.querySelector('.meta').getBoundingClientRect();
const cards = Array.from(document.querySelectorAll('aside .card'));
const footer = document.querySelector('.aside-foot').getBoundingClientRect();
return {mainBottom:last.bottom-sheet.top,lastCardBottom:cards.at(-1).getBoundingClientRect().bottom-sheet.top,
footerTop:footer.top-sheet.top,pageHeight:sheet.height};
}''')
                assert overview_bounds['mainBottom'] < overview_bounds['pageHeight']-45, 'Overview main content overflows'
                assert overview_bounds['lastCardBottom'] < overview_bounds['footerTop']-8, 'Overview sidebar overlaps footer'
                await tab.pdf(path=str(work/'overview.pdf'),print_background=True,prefer_css_page_size=True)
            answer_positions = await render_answers(tab, questions, source[0].rect.width + width,
                                                    source[0].rect.height, work)
            await browser.close()
        sidebars = fitz.open(stream=(work/'sidebars.pdf').read_bytes(),filetype='pdf')
        if len(sidebars) != len(source):
            raise ValueError(f'Sidebar pagination mismatch: {len(sidebars)} vs {len(source)}')
        result = fitz.open()
        overview_count = 0
        if (work/'overview.pdf').exists():
            with fitz.open(work/'overview.pdf') as overview:
                assert len(overview)==1, 'The opening overview must fit on one page'
                result.insert_pdf(overview)
                overview_count = len(overview)
        for i, old in enumerate(source):
            w,h = old.rect.width,old.rect.height
            page = result.new_page(width=w+width,height=h)
            page.show_pdf_page(fitz.Rect(0,0,w,h),source,i)
            page.show_pdf_page(fitz.Rect(w,0,w+width,h),sidebars,i)
            # show_pdf_page preserves vector/text content but not link annotations.
            for link in old.get_links():
                if link['kind'] == fitz.LINK_URI:
                    page.insert_link({'kind':fitz.LINK_URI,'from':link['from'],'uri':link['uri']})
                elif link['kind'] == fitz.LINK_GOTO:
                    # Resolve internal links after all pages exist, below.
                    pass
        answer_start = len(result)
        answer_count = 0
        if questions:
            with fitz.open(work / 'answers.pdf') as answers:
                result.insert_pdf(answers)
                answer_count = len(answers)
        sidebar_navigation = []
        prompt_positions = {card['id']: (item['page'], card)
                            for item in layout for card in item['cards']}
        for answer in answer_positions:
            body_page, card = prompt_positions[answer['id']]
            prompt_page = body_page - 1 + overview_count
            answer_page = answer_start + answer['page'] - 1
            source_width = source[body_page - 1].rect.width
            prompt_to = [source_width + card['left'], card['top']]
            rect = card['answer_rect']
            forward_rect = [source_width + rect[0], rect[1], source_width + rect[2], rect[3]]
            forward = {'source_pdf_page': prompt_page + 1, 'target_pdf_page': answer_page + 1,
                       'from': forward_rect, 'to': answer['to']}
            backward = {'source_pdf_page': answer_page + 1, 'target_pdf_page': prompt_page + 1,
                        'from': answer['return_rect'], 'to': prompt_to}
            for link in (forward, backward):
                result[link['source_pdf_page'] - 1].insert_link({
                    'kind': fitz.LINK_GOTO, 'from': fitz.Rect(link['from']),
                    'page': link['target_pdf_page'] - 1, 'to': fitz.Point(link['to'])})
            sidebar_navigation.append({'id': answer['id'], 'forward': forward, 'return': backward})
        internal_links = []
        for i,old in enumerate(source):
            for link in old.get_links():
                if link['kind'] == fitz.LINK_NAMED and link.get('nameddest'):
                    # Chromium emits named destinations. Resolve through MuPDF:
                    # get_links()['to'] can still use bottom-left PDF coordinates.
                    target, x, y = source.resolve_link('#nameddest=' + quote(link['nameddest'], safe=''))
                    point = fitz.Point(x, y)
                elif link['kind'] == fitz.LINK_GOTO and link.get('page', -1) >= 0:
                    target = link['page']
                    point = link.get('to', fitz.Point(0, 0))
                else:
                    continue
                if not 0 <= target < len(source):
                    raise ValueError(f'Cannot resolve internal link on source page {i + 1}: {link}')
                destination = {'kind':fitz.LINK_GOTO, 'from':link['from'],
                    'page':target + overview_count, 'to':point}
                result[i + overview_count].insert_link(destination)
                internal_links.append({'source_pdf_page':i + overview_count + 1,
                    'target_pdf_page':target + overview_count + 1,
                    'from':list(link['from']), 'to':list(point)})
        result.set_metadata({'title':data['title'],'author':'学习讲义',
            'subject':data['method_source'],'keywords':'学习讲义,内容总览,回想,组块,间隔练习'})
        result.set_toc(([[1,'00 学习总览：整体关系与学习路线',1]] if overview_count else [])+
            [[1,f"{item['page']:02d} {item['title']}",item['page']+overview_count] for item in data['pages']] +
            ([[1, '参考答案', answer_start + 1]] if answer_count else []))
        out_path.parent.mkdir(parents=True,exist_ok=True)
        result.save(out_path,garbage=4,deflate=True)
        result.close()
        sidebars.close()
    final = fitz.open(out_path)
    for expected in internal_links + [link for pair in sidebar_navigation for link in (pair['forward'], pair['return'])]:
        actual = final[expected['source_pdf_page'] - 1].get_links()
        assert any(link['kind'] == fitz.LINK_GOTO
            and link.get('page') == expected['target_pdf_page'] - 1
            and max(abs(a-b) for a,b in zip(link['from'], expected['from'])) < 0.1
            and max(abs(a-b) for a,b in zip(link.get('to', (float('inf'),)*2), expected['to'])) < 0.1
            for link in actual), f'Internal link missing or changed: {expected}'
    answer_text = ''.join(''.join(page.get_text().split()) for page in list(final)[answer_start:])
    for question in questions:
        for paragraph in question['answer'] + question['check']:
            assert ''.join(paragraph.split()) in answer_text, f'Answer text missing: {question["id"]}'
    checks=[]
    for i,old in enumerate(source):
        # Preserve every source character and exact visible pixels in the left panel.
        before=''.join(old.get_text().split())
        after=''.join(final[i+overview_count].get_text(clip=old.rect).split())
        assert before==after, f'Source text changed on page {i+1}'
        left_before=old.get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
        left_after=final[i+overview_count].get_pixmap(matrix=fitz.Matrix(1,1),clip=old.rect,alpha=False)
        assert left_before.samples==left_after.samples, f'Source rendering changed on page {i+1}'
        for card in data['pages'][i]['cards']:
            assert card['id'] in final[i+overview_count].get_text(), f'Missing card {card["id"]}'
        checks.append({'body_page':i+1,'pdf_page':i+1+overview_count,'source_text_identical':True,'source_pixels_identical':True})
    report={'output':str(out_path),'page_count':len(final),'overview_pages':overview_count,'sidebar_width_mm':width*25.4/72,
            'source_preservation':checks,'internal_links_preserved':internal_links,'layout':layout,
            'answer_pages':answer_count,'sidebar_navigation':sidebar_navigation,
            'legacy_untyped_cards':[card['id'] for page in data['pages'] for card in page['cards']
                                    if 'kind' not in card]}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True,exist_ok=True)
        for i,p in enumerate(final):
            p.get_pixmap(matrix=fitz.Matrix(1.1,1.1)).save(args.preview_dir/f'page-{i+1:02d}.png')
    print(json.dumps({'output':str(out_path),'pages':len(final),'bytes':out_path.stat().st_size,
        'source_text_and_pixels':'identical','answer_pages':answer_count,
        'sidebar_answer_links':len(sidebar_navigation) * 2,'report':str(args.report)},ensure_ascii=False))
    source.close()
    final.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--notes',type=Path,required=True)
    parser.add_argument('--overview',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--preview-dir',type=Path)
    asyncio.run(build(parser.parse_args()))
