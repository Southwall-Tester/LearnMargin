import { useCallback, useEffect, useRef, useState } from 'react';
import type { MouseEvent } from 'react';

export const sectionIds = ['materials', 'scope', 'learning', 'result-panel'] as const;
export type SectionId = typeof sectionIds[number];

function hashSection(): SectionId | undefined {
  const id = window.location.hash.slice(1);
  return sectionIds.find(section => section === id);
}

/** The preview is beside the editor on desktop, not a fourth vertical step. */
function sectionAtViewport(): SectionId {
  const sections = sectionIds.flatMap(id => {
    const element = document.getElementById(id);
    return element ? [{ id, rect: element.getBoundingClientRect() }] : [];
  });
  const materials = sections.find(section => section.id === 'materials');
  const preview = sections.find(section => section.id === 'result-panel');
  const sideBySide = materials && preview && materials.rect.width > 0
    && (preview.rect.left >= materials.rect.right - 1 || materials.rect.left >= preview.rect.right - 1);
  const candidates = sideBySide ? sections.filter(section => section.id !== 'result-panel') : sections;
  if (!candidates.length || window.scrollY <= 0) return 'materials';
  const atBottom = window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
  if (atBottom) return candidates[candidates.length - 1].id;
  const readingLine = Math.min(160, window.innerHeight / 4);
  return candidates.reduce((current, section) => section.rect.top <= readingLine ? section.id : current, candidates[0].id);
}

export default function useSectionNavigation() {
  const [activeSection, setActiveSection] = useState<SectionId>(() => hashSection() ?? 'materials');
  // Keep the clicked target during smooth scrolling and when two columns share a row.
  // The next manual page scroll releases this preference.
  const selected = useRef<{ id: SectionId; scrolling: boolean } | null>(null);
  const manualInput = useRef(false);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const frame = useRef<number | undefined>(undefined);

  const settle = useCallback(() => {
    clearTimeout(settleTimer.current);
    settleTimer.current = setTimeout(() => {
      if (selected.current) selected.current.scrolling = false;
    }, 160);
  }, []);

  const navigate = useCallback((id: SectionId, behavior: ScrollBehavior = 'smooth', updateHash = false) => {
    const target = document.getElementById(id);
    if (!target) return;
    manualInput.current = false;
    selected.current = { id, scrolling: true };
    setActiveSection(id);
    if (updateHash && window.location.hash !== `#${id}`) window.history.pushState(null, '', `#${id}`);
    target.scrollIntoView({ behavior, block: 'start' });
    settle();
  }, [settle]);

  useEffect(() => {
    let previousY = window.scrollY;
    const update = () => {
      frame.current = undefined;
      const preference = selected.current;
      if (preference) {
        const rect = document.getElementById(preference.id)?.getBoundingClientRect();
        if (preference.scrolling || (rect && rect.bottom > 0 && rect.top < window.innerHeight)) return;
        selected.current = null;
      }
      setActiveSection(sectionAtViewport());
    };
    const schedule = () => {
      if (frame.current === undefined) frame.current = window.requestAnimationFrame(update);
    };
    const scroll = () => {
      // Nested previews have their own scroll position and do not navigate the workspace.
      if (previousY === window.scrollY) return;
      previousY = window.scrollY;
      if (manualInput.current) { selected.current = null; manualInput.current = false; }
      else if (selected.current?.scrolling) settle();
      else selected.current = null;
      schedule();
    };
    const manualScroll = () => {
      // Do not change selection until the main page actually moves. A wheel/touch
      // inside a source or PDF preview may only scroll that nested reader.
      manualInput.current = true;
    };
    const keydown = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && target.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) manualScroll();
    };
    const hashChanged = () => {
      const id = hashSection();
      if (id) navigate(id, 'auto');
      else { selected.current = null; schedule(); }
    };
    window.addEventListener('scroll', scroll, { passive: true });
    window.addEventListener('resize', schedule);
    window.addEventListener('wheel', manualScroll, { passive: true });
    window.addEventListener('touchstart', manualScroll, { passive: true });
    window.addEventListener('pointerdown', manualScroll, { passive: true });
    window.addEventListener('keydown', keydown);
    window.addEventListener('hashchange', hashChanged);
    window.addEventListener('popstate', hashChanged);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
    for (const selector of ['.main-content', '.workspace-editor', ...sectionIds.map(id => `#${id}`)]) {
      const element = document.querySelector(selector);
      if (element) observer?.observe(element);
    }
    if (hashSection()) hashChanged();
    else schedule();
    return () => {
      window.removeEventListener('scroll', scroll);
      window.removeEventListener('resize', schedule);
      window.removeEventListener('wheel', manualScroll);
      window.removeEventListener('touchstart', manualScroll);
      window.removeEventListener('pointerdown', manualScroll);
      window.removeEventListener('keydown', keydown);
      window.removeEventListener('hashchange', hashChanged);
      window.removeEventListener('popstate', hashChanged);
      observer?.disconnect();
      clearTimeout(settleTimer.current);
      if (frame.current !== undefined) window.cancelAnimationFrame(frame.current);
      frame.current = undefined;
    };
  }, [navigate, settle]);

  function navigationProps(id: SectionId) {
    return {
      href: `#${id}`,
      'aria-current': activeSection === id ? 'location' as const : undefined,
      onClick: (event: MouseEvent<HTMLAnchorElement>) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(id, 'smooth', true);
      },
    };
  }

  return { activeSection, navigate, navigationProps };
}
