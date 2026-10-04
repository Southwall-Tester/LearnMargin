import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import useSectionNavigation, { sectionIds } from './useSectionNavigation';

let stacked: boolean;
let materialsHeight: number;
let resizeCallback: (() => void) | undefined;

function Harness() {
  const { navigationProps } = useSectionNavigation();
  return <main className="main-content"><nav>{sectionIds.map(id => <a key={id} {...navigationProps(id)}>{id}</a>)}</nav>
    <div className="workspace-editor">{sectionIds.slice(0, 3).map(id => <section key={id} id={id} />)}</div>
    <aside id="result-panel"><div data-testid="preview">Preview</div></aside>
  </main>;
}

function current() {
  const matches = document.querySelectorAll('a[aria-current="location"]');
  expect(matches).toHaveLength(1);
  return matches[0].getAttribute('href');
}

function flush() { act(() => { vi.advanceTimersByTime(20); }); }

function scrollTo(y: number, manual = true) {
  if (manual) fireEvent.wheel(window, { deltaY: y - window.scrollY });
  Object.defineProperty(window, 'scrollY', { configurable: true, value: y });
  fireEvent.scroll(window);
  flush();
}

beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState(null, '', '/');
  Object.defineProperty(window, 'scrollY', { configurable: true, value: 0 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 700 });
  Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, get: () => stacked ? 2200 : 1800 });
  stacked = false;
  materialsHeight = 400;
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => window.setTimeout(() => callback(0), 1));
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(id => window.clearTimeout(id));
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const top = this.id === 'materials' ? 80 : this.id === 'scope' ? 100 + materialsHeight
      : this.id === 'learning' ? 550 + materialsHeight : stacked ? 1500 : 80;
    const left = this.id === 'result-panel' && !stacked ? 800 : 250;
    const height = this.id === 'materials' ? materialsHeight : this.id === 'result-panel' ? 700 : 400;
    return { top: top - window.scrollY, bottom: top + height - window.scrollY, left, right: left + 500, width: 500, height, x: left, y: top - window.scrollY, toJSON() {} };
  });
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resizeCallback = callback; }
    observe() {} disconnect() {}
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('section navigation', () => {
  it('tracks manual scrolling both ways without the adjacent preview stealing selection', () => {
    render(<Harness />); flush();
    expect(current()).toBe('#materials');
    scrollTo(410); expect(current()).toBe('#scope');
    scrollTo(850); expect(current()).toBe('#learning');
    scrollTo(450); expect(current()).toBe('#scope');
    scrollTo(0); expect(current()).toBe('#materials');
  });

  it('keeps a clicked preview selected through smooth scrolling, then follows manual page scrolling', () => {
    render(<Harness />); flush();
    scrollTo(850);
    fireEvent.click(screen.getByRole('link', { name: 'result-panel' }));
    expect(current()).toBe('#result-panel');
    expect(window.location.hash).toBe('#result-panel');
    scrollTo(450, false); expect(current()).toBe('#result-panel');
    scrollTo(80, false); expect(current()).toBe('#result-panel');
    act(() => { vi.advanceTimersByTime(200); });
    fireEvent.wheel(screen.getByTestId('preview'), { deltaY: 100 });
    fireEvent.scroll(screen.getByTestId('preview'));
    act(() => { resizeCallback?.(); }); flush();
    expect(current()).toBe('#result-panel');
    scrollTo(410); expect(current()).toBe('#scope');
  });

  it('interrupts smooth navigation using keyboard and scrollbar movement', () => {
    render(<Harness />); flush();
    fireEvent.click(screen.getByRole('link', { name: 'result-panel' }));
    fireEvent.keyDown(window, { key: 'PageDown' });
    scrollTo(410, false); expect(current()).toBe('#scope');
    fireEvent.click(screen.getByRole('link', { name: 'learning' }));
    fireEvent.pointerDown(document.documentElement);
    scrollTo(450, false); expect(current()).toBe('#scope');
  });

  it('keeps clicked sections selected when the document end limits anchor alignment', () => {
    render(<Harness />); flush();
    fireEvent.click(screen.getByRole('link', { name: 'scope' }));
    scrollTo(1100, false);
    expect(current()).toBe('#scope');
  });

  it('includes the preview in stacked layout and recalculates when resizing or content height changes', () => {
    render(<Harness />); flush();
    scrollTo(850); expect(current()).toBe('#learning');
    materialsHeight = 900;
    act(() => { resizeCallback?.(); }); flush();
    expect(current()).toBe('#scope');
    materialsHeight = 400;
    stacked = true;
    fireEvent.resize(window); flush();
    scrollTo(1400); expect(current()).toBe('#result-panel');
    scrollTo(850); expect(current()).toBe('#learning');
    stacked = false;
    fireEvent.resize(window); flush();
    expect(current()).toBe('#learning');
  });

  it('restores anchor navigation from an initial hash and browser history', () => {
    window.history.replaceState(null, '', '/#learning');
    render(<Harness />); flush();
    expect(current()).toBe('#learning');
    window.history.replaceState(null, '', '/#scope');
    fireEvent.popState(window); flush();
    expect(current()).toBe('#scope');
    window.history.replaceState(null, '', '/#result-panel');
    fireEvent(window, new HashChangeEvent('hashchange')); flush();
    expect(current()).toBe('#result-panel');
  });
});
