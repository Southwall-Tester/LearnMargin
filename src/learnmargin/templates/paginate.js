/* Content is already escaped by the Markdown renderer; only bundled code executes. */
(async function () {
  "use strict";
  const pages = document.getElementById("pages");
  const flow = document.getElementById("flow");
  const pausePlan = JSON.parse(flow.dataset.pausePlan || "[]");
  const cleanText = node => node.textContent.replace(/\s+/g, "");
  const errors = [];
  try {
    const maths = [...flow.querySelectorAll(".math")];
    for (const element of maths) {
      try {
        katex.render(element.dataset.tex, element, {
          displayMode: element.dataset.display === "true", throwOnError: true,
          trust: false, strict: "ignore", maxExpand: 1000, maxSize: 30,
          output: "htmlAndMathml"
        });
      } catch (error) { errors.push(String(error.message)); }
    }
    if (errors.length) throw new Error("公式无法排版：" + errors[0].slice(0, 220));
    await document.fonts.ready;
    let body, currentSheet, label = "", pageCount = 0;
    function newPage() {
      if (++pageCount > 200) throw new Error("讲义超过 200 页，请缩小学习范围。");
      currentSheet = document.createElement("article");
      currentSheet.className = "sheet";
      body = document.createElement("div");
      body.className = "page-body";
      const footer = document.createElement("footer");
      footer.className = "page-footer";
      const name = document.createElement("span");
      name.className = "footer-label";
      name.textContent = "LearnMargin · " + label;
      const number = document.createElement("span");
      number.className = "page-number";
      footer.append(name, number);
      currentSheet.append(body, footer);
      pages.append(currentSheet);
    }
    function overflow() { return body.scrollHeight > body.clientHeight + 1; }
    function removeIds(element) {
      element.removeAttribute("id");
      element.querySelectorAll("[id]").forEach(node => node.removeAttribute("id"));
    }
    function splitter(row) {
      const parts = [...row.children];
      const main = row.querySelector(".main"), aside = row.querySelector("aside");
      // Explicit after_* guidance belongs to the completed block, just like
      // its planned pause. Keep the entire group on the final fragment.
      const endCards = [...(aside?.children || [])].filter(child =>
        child.matches(".study-end, .pause[data-pause-kind]"));
      const hasEnd = endCards.length > 0;
      const promptHeight = hasEnd ? [...aside.children].filter(child => !endCards.includes(child))
        .reduce((height, child) => height + child.getBoundingClientRect().height, 0) : 0;
      const tall = hasEnd ? (promptHeight > main.getBoundingClientRect().height ? aside : main) :
        parts.reduce((a, b) => a.getBoundingClientRect().height >= b.getBoundingClientRect().height ? a : b);
      function textParts(element) {
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        const nodes = []; let node, total = 0;
        while ((node = walker.nextNode())) {
          if (!node.parentElement.closest(".math, .study-end, .pause[data-pause-kind]") && node.textContent.length) {
            nodes.push({node, start: total}); total += node.textContent.length;
          }
        }
        return {total, at(target) {
          const point = nodes.find(item => item.start + item.node.textContent.length >= target);
          const offset = Math.max(1, target - point.start);
          const before = document.createRange();
          before.selectNodeContents(element); before.setEnd(point.node, offset);
          const after = document.createRange();
          after.selectNodeContents(element); after.setStart(point.node, offset);
          return [before.cloneContents(), after.cloneContents()];
        }};
      }
      const primary = textParts(tall), total = primary.total;
      const mainParts = hasEnd && tall === aside ? textParts(main) : null;
      if (total < 30) throw new Error("单个公式、图表或内容块过高，无法安全分页，请拆分内容。");
      function at(target) {
      const fragments = primary.at(target);
      const first = row.cloneNode(true), second = row.cloneNode(true);
      const position = parts.indexOf(tall);
      first.children[position].replaceChildren(fragments[0]);
      second.children[position].replaceChildren(fragments[1]);
      [...second.children].forEach((child, index) => { if (index !== position) child.replaceChildren(); });
      // Continuations do not duplicate anchors. End-card anchors are moved
      // below after stripping clones, so their return links still hit the card.
      removeIds(second);
      let deferredMainId = null;
      if (hasEnd) {
        if (mainParts) {
          // A long study question may itself span pages. Split it normally,
          // retaining a real ending of the main text beside the final cards.
          // Once that short tail is reserved, carry it intact through any
          // further sidebar-only continuations rather than shaving it away.
          const retained = Math.min(80, Math.ceil(mainParts.total / 2));
          const cut = row.dataset.pauseMainTail || mainParts.total < 2 ? 0 :
            Math.max(1, Math.min(mainParts.total - retained, Math.floor(mainParts.total * target / total)));
          if (cut) {
            const content = mainParts.at(cut);
            first.querySelector(".main").replaceChildren(content[0]);
            second.querySelector(".main").replaceChildren(content[1]);
          } else {
            first.querySelector(".main").replaceChildren();
            second.querySelector(".main").replaceChildren(...main.cloneNode(true).childNodes);
            deferredMainId = main.id;
            first.querySelector(".main").removeAttribute("id");
          }
          if (mainParts.total - cut <= 80) second.dataset.pauseMainTail = "true";
        }
        for (const fragment of [first, second])
          fragment.querySelectorAll(".study-end, .pause[data-pause-kind]").forEach(card => card.remove());
        first.classList.remove("pause-attached", "study-end-attached");
        second.querySelector("aside").append(...endCards.map(card => card.cloneNode(true)));
      }
      if (deferredMainId) second.querySelector(".main").id = deferredMainId;
      return [first, second];
      }
      return {total, at, minimumTail: hasEnd && tall === main ? Math.min(80, total) : 1};
    }
    function widenIfNeeded(row) {
      // A formula or a broad table may use the complete A4 text area.
      const main = row.querySelector(".main");
      if (main && (main.scrollWidth > main.clientWidth + 2 ||
          [...main.querySelectorAll("table")].some(table => table.scrollWidth > main.clientWidth + 2))) {
        row.classList.add("full");
      }
    }
    function put(row) {
      body.append(row);
      widenIfNeeded(row);
      const beforeWide = row.classList.contains("full") && row.querySelector(
        ':scope > aside > .study-start[data-explicit-placement="true"]');
      if (beforeWide) {
        // A wide formula uses both columns. Its explicit before-card must
        // still precede it, rather than falling below it with the whole aside.
        // Keep origin markers so preservation checks cover the moved text.
        row.remove();
        const lead = row.cloneNode(false), emptyMain = row.querySelector(".main").cloneNode(false);
        const rail = row.querySelector("aside").cloneNode(false);
        lead.className = "row keep-next study-before-wide";
        emptyMain.removeAttribute("id");
        rail.append(beforeWide); lead.append(emptyMain, rail);
        put(lead); put(row); return;
      }
      if (!overflow()) return;
      const wasFirst = body.children.length === 1;
      const height = row.getBoundingClientRect().height;
      const space = body.getBoundingClientRect().bottom - row.getBoundingClientRect().top;
      // Ordinary short blocks stay intact. Long prose uses the remaining page space.
      function moveToNextPage() {
        row.remove();
        const heading = body.lastElementChild?.classList.contains("keep-next") && body.children.length > 1 ?
          body.lastElementChild : null;
        if (heading) heading.remove();
        newPage();
        if (heading) body.append(heading);
        return put(row);
      }
      if (!wasFirst && ((!row.classList.contains("splittable") && height < body.clientHeight * .65) || space < 100)) {
        return moveToNextPage();
      }
      let candidates;
      try { candidates = splitter(row); }
      catch (error) { if (!wasFirst) return moveToNextPage(); else throw error; }
      row.remove();
      let low = 1, high = candidates.total - candidates.minimumTail, best = null;
      while (low <= high) {
        const mid = Math.floor((low + high) / 2);
        const pair = candidates.at(mid);
        body.append(pair[0]);
        const fits = !overflow();
        pair[0].remove();
        if (fits) { best = pair; low = mid + 1; }
        else high = mid - 1;
      }
      if (!best) {
        if (!wasFirst) { body.append(row); return moveToNextPage(); }
        throw new Error("单个内容块无法安全分页，请拆分过长的侧栏或公式。");
      }
      body.append(best[0]);
      newPage(); put(best[1]);
    }
    function paginate() {
    pages.replaceChildren(); body = undefined; currentSheet = undefined; pageCount = 0;
    let previousRole = "", previousLabel = "";
    for (const section of flow.children) {
      label = section.dataset.label;
      // Do not strand the tail of the overview's navigation on a near-empty page.
      const filled = body?.lastElementChild ? body.lastElementChild.getBoundingClientRect().bottom -
        body.getBoundingClientRect().top : 0;
      const shareNavigationTail = previousRole === "overview" && body.querySelector(".toc-row") &&
        (filled < body.clientHeight * .36 || [...body.children].every(row => row.classList.contains("toc-row")));
      const shareAnswerTail = previousRole === "answers" && section.dataset.role === "review" && filled < body.clientHeight * .36;
      const shareTail = shareNavigationTail || shareAnswerTail;
      if (!body || (section.dataset.newPage === "true" && body.children.length && !shareTail)) newPage();
      else if (shareTail) currentSheet.querySelector(".footer-label").textContent =
        "LearnMargin · " + previousLabel + " / " + label;
      for (const sourceRow of section.querySelectorAll(":scope > .row")) {
        const row = sourceRow.cloneNode(true);
        if (section.dataset.section) row.dataset.section = section.dataset.section;
        // Keep a label with at least the first few lines of the following content.
        if (row.classList.contains("keep-next") && body.children.length) {
          body.append(row);
          const remaining = body.getBoundingClientRect().bottom - row.getBoundingClientRect().bottom;
          row.remove();
          if (remaining < 55) newPage();
        }
        put(row);
      }
      previousRole = section.dataset.role || "";
      previousLabel = label;
    }
    }
    // Python selects complete learning-task boundaries before layout. A4 and
    // wide pages render that same plan; page count is never a study timer.
    const originalText = new Map();
    function recordContent() {
    originalText.clear();
    [...flow.querySelectorAll(".row > .main, .row > aside")].forEach((node, index) => {
      node.dataset.origin = String(index);
      originalText.set(String(index), cleanText(node));
    });
    }
    recordContent();
    paginate();
    const renderedText = new Map();
    pages.querySelectorAll("[data-origin]").forEach(node => {
      const key = node.dataset.origin;
      renderedText.set(key, (renderedText.get(key) || "") + cleanText(node));
    });
    const preserved = [...originalText].every(([key, text]) => renderedText.get(key) === text);
    if (!preserved) throw new Error("分页改变了正文或侧栏内容，已停止导出。");
    const overflows = [];
    [...pages.children].forEach((sheet, index) => {
      sheet.querySelector(".page-number").textContent = (index + 1) + " / " + pageCount;
      const area = sheet.querySelector(".page-body").getBoundingClientRect();
      for (const element of sheet.querySelectorAll(".row, .main, aside, .katex-display, table, pre")) {
        const rect = element.getBoundingClientRect();
        if (rect.right > area.right + 2 || rect.bottom > area.bottom + 2 ||
            (element.scrollWidth > element.clientWidth + 3 && element.clientWidth > 0))
          overflows.push({page: index + 1, element: element.className || element.tagName});
      }
    });
    flow.remove();
    const links = [...pages.querySelectorAll('a[href^="#"]')];
    for (const link of links) {
      if (!document.getElementById(link.getAttribute("href").slice(1))) throw new Error("内部导航目标缺失。");
    }
    const answerPage = [...pages.children].findIndex(sheet => sheet.querySelector("#answers")) + 1;
    const anchorPositions = {};
    [...pages.children].forEach((sheet, index) => {
      const bounds = sheet.getBoundingClientRect();
      sheet.querySelectorAll("[id]").forEach(element => {
        const rect = element.getBoundingClientRect();
        anchorPositions[element.id] = {page: index + 1, left: (rect.left - bounds.left) * .75,
          top: (bounds.height - (rect.top - bounds.top)) * .75};
      });
    });
    const navigationOnlyPages = [...pages.children].flatMap((sheet, index) => {
      const rows = [...sheet.querySelector(".page-body").children];
      return rows.length && rows.every(row => row.classList.contains("toc-row")) ? [index + 1] : [];
    });
    const orphanHeadingPages = [...pages.children].flatMap((sheet, index) =>
      sheet.querySelector(".page-body").lastElementChild?.classList.contains("keep-next") ? [index + 1] : []);
    const pauseOnlyPages = [...pages.children].flatMap((sheet, index) => {
      const rows = [...sheet.querySelector(".page-body").children];
      return rows.length && rows.every(row => row.classList.contains("pause-row")) ? [index + 1] : [];
    });
    const pausePositions = [...pages.querySelectorAll(".pause[data-pause-kind]")].map(card => {
      const row = card.closest(".row"), main = row.querySelector(".main");
      const sheet = row.closest(".sheet"), bounds = sheet.getBoundingClientRect();
      const rect = card.getBoundingClientRect(), area = sheet.querySelector(".page-body").getBoundingClientRect();
      const rail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--rail-width")) * 96 / 25.4;
      const peers = [...pages.querySelectorAll(`.row[data-section="${row.dataset.section}"][data-boundary="${row.dataset.boundary}"]`)];
      return {section: Number(row.dataset.section), kind: card.dataset.pauseKind, boundary: row.dataset.boundary,
        page: [...pages.children].indexOf(sheet) + 1, left_pt: (rect.left - bounds.left) * .75,
        top_pt: (rect.top - bounds.top) * .75, height_pt: rect.height * .75,
        right_rail: rect.left >= area.right - rail - 2, attached_content_characters: cleanText(main).length,
        at_boundary_end: peers.at(-1) === row, orphaned: !cleanText(main).length || card.parentElement.tagName !== "ASIDE"};
    });
    if (pausePositions.some(position => position.orphaned || !position.right_rail || !position.at_boundary_end))
      throw new Error("休息提示未与完整内容边界对齐，已停止导出。");
    if (pausePositions.length !== pausePlan.length || pausePlan.some(planned =>
      pausePositions.filter(position => position.section === planned.section &&
        position.boundary === planned.boundary && position.kind === planned.kind).length !== 1))
      throw new Error("休息提示与学习任务计划不一致，已停止导出。");
    window.learnmarginReport = {page_count: pageCount, math_count: pages.querySelectorAll(".math").length, overflow: overflows,
      content_preserved: preserved, internal_links: links.length, answer_section_page: answerPage,
      navigation_only_pages: navigationOnlyPages, orphan_heading_pages: orphanHeadingPages,
      pause_only_pages: pauseOnlyPages, pause_positions: pausePositions, pause_plan: pausePlan,
      anchor_positions: anchorPositions};
  } catch (error) {
    window.learnmarginReport = {error: String(error.message)};
    const notice = document.createElement("pre"); notice.textContent = "排版失败：" + error.message;
    pages.append(notice);
  }
})();
