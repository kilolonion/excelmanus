/**
 * @jest-environment jsdom
 */
import { renderHook } from '@testing-library/react';
import { useIsMobile, useIsTablet, useIsDesktop, useIsMediumScreen } from '../use-mobile';

// 模拟 window.matchMedia
const mockMatchMedia = (matches: boolean) => ({
  matches,
  media: '',
  onchange: null,
  addListener: jest.fn(),
  removeListener: jest.fn(),
  addEventListener: jest.fn(),
  removeEventListener: jest.fn(),
  dispatchEvent: jest.fn(),
});

describe('响应式断点 hooks', () => {
  beforeEach(() => {
    // 重置 window.matchMedia 模拟
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: jest.fn().mockImplementation(() => mockMatchMedia(false)),
    });
  });

  describe('useIsMobile', () => {
    it('在移动端屏幕尺寸下返回 true', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 600,
      });
      
      window.matchMedia = jest.fn().mockImplementation((query) => 
        mockMatchMedia(query.includes('max-width: 767px'))
      );

      const { result } = renderHook(() => useIsMobile());
      expect(result.current).toBe(true);
    });

    it('在桌面端屏幕尺寸下返回 false', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 1400,
      });
      
      window.matchMedia = jest.fn().mockImplementation(() => mockMatchMedia(false));

      const { result } = renderHook(() => useIsMobile());
      expect(result.current).toBe(false);
    });
  });

  describe('useIsDesktop', () => {
    it('在桌面端屏幕尺寸下返回 true', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 1400,
      });
      
      window.matchMedia = jest.fn().mockImplementation((query) => 
        mockMatchMedia(query.includes('min-width: 1280px'))
      );

      const { result } = renderHook(() => useIsDesktop());
      expect(result.current).toBe(true);
    });

    it('在移动端屏幕尺寸下返回 false', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 600,
      });
      
      window.matchMedia = jest.fn().mockImplementation(() => mockMatchMedia(false));

      const { result } = renderHook(() => useIsDesktop());
      expect(result.current).toBe(false);
    });
  });

  describe('useIsMediumScreen', () => {
    it('在中等屏幕尺寸下返回 true', () => {
      Object.defineProperty(window, 'innerWidth', {
        writable: true,
        configurable: true,
        value: 1100,
      });
      
      window.matchMedia = jest.fn().mockImplementation((query) => 
        mockMatchMedia(query.includes('min-width: 1024px') && query.includes('max-width: 1279px'))
      );

      const { result } = renderHook(() => useIsMediumScreen());
      expect(result.current).toBe(true);
    });
  });
});