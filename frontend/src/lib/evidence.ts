import type { ValidationState } from "@/lib/types";

export interface Span {
  id: string;
  start: number;
  end: number;
  state: ValidationState;
}

export interface Segment {
  text: string;
  ids: string[];
}

/** Split a document into segments so each one knows which evidence spans cover it (spans may overlap). */
export function buildSegments(text: string, spans: Span[]): Segment[] {
  const valid = spans.filter((s) => s.start >= 0 && s.end <= text.length && s.start < s.end);
  const points = new Set<number>([0, text.length]);
  for (const s of valid) {
    points.add(s.start);
    points.add(s.end);
  }
  const sorted = [...points].sort((a, b) => a - b);
  const segments: Segment[] = [];
  for (let i = 0; i < sorted.length - 1; i++) {
    const [a, b] = [sorted[i], sorted[i + 1]];
    if (a === b) continue;
    segments.push({
      text: text.slice(a, b),
      ids: valid.filter((s) => s.start <= a && s.end >= b).map((s) => s.id),
    });
  }
  return segments;
}

export const STATE_LABEL: Record<ValidationState, string> = {
  unverified: "Needs review",
  verified: "Verified",
  corrected: "Corrected",
  rejected: "Rejected",
};

export function confidenceText(c: number) {
  return `${Math.round(c * 100)}%`;
}
