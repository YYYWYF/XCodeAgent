import { MAX_WIDTH_RATIO, MIN_WIDTH } from '../constants';

export function clampWidth(w: number) {
  const max = window.innerWidth * MAX_WIDTH_RATIO;
  return Math.max(MIN_WIDTH, Math.min(w, max));
}
