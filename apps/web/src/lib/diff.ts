/** Word-level diff (longest common subsequence). Used to show résumé edits. */

export interface DiffPart {
  op: "same" | "add" | "del";
  text: string;
}

const tokenise = (s: string) => s.split(/(\s+)/).filter((t) => t.length > 0);

export function diffWords(before: string, after: string): DiffPart[] {
  const a = tokenise(before);
  const b = tokenise(after);
  const n = a.length;
  const m = b.length;
  // lcs[i][j] = LCS length of a[i..] and b[j..]
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const parts: DiffPart[] = [];
  const push = (op: DiffPart["op"], text: string) => {
    const last = parts[parts.length - 1];
    if (last && last.op === op) last.text += text;
    else parts.push({ op, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      push("same", a[i]!);
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      push("del", a[i]!);
      i++;
    } else {
      push("add", b[j]!);
      j++;
    }
  }
  while (i < n) push("del", a[i++]!);
  while (j < m) push("add", b[j++]!);
  return parts;
}

export function hasChanges(parts: DiffPart[]): boolean {
  return parts.some((p) => p.op !== "same");
}
