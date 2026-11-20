// Mirrors apps/api/app/services/karaoke.py (word_weight / pause_after / proportional word_times),
// so the editor preview lights words up like the burned-in video.

function wordWeight(word: string): number {
  const digits = (word.match(/\d/g) ?? []).length;
  const letters = (word.match(/\p{L}/gu) ?? []).length;
  let weight = digits ? Math.max(1, digits * 1.5) : 1;
  if (letters > 7) weight += (letters - 7) / 4;
  return weight;
}

function pauseAfter(word: string): number {
  const tail = word.replace(/["'”’)»\]]+$/, "");
  if (/[.!?…。！？]$/.test(tail)) return 0.8;
  if (/[,;:—–，]$/.test(tail)) return 0.45;
  return 0;
}

/** [start, end] in seconds for each word, shared over the span by spoken syllables. */
export function wordTimes(words: string[], start: number, end: number): [number, number][] {
  const marks: [number, number][] = [];
  let cursor = 0;
  words.forEach((word, index) => {
    const weight = wordWeight(word);
    marks.push([cursor, cursor + weight]);
    cursor += weight + (index < words.length - 1 ? pauseAfter(word) : 0);
  });
  const total = cursor || 1;
  const span = Math.max(0.05, end - start);
  return marks.map(([a, b]) => [start + (span * a) / total, start + (span * b) / total]);
}
