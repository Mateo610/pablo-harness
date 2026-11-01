// Mirror of src/pablo/hunks.py: same parsing, same "<file>:<hunk>" ids.

export interface Hunk {
  id: string;
  header: string;
  lines: string[];
  added: number;
  removed: number;
}

export interface FilePatch {
  path: string;
  hunks: Hunk[];
}

export function parseDiff(diff: string): FilePatch[] {
  const files: FilePatch[] = [];
  let current: FilePatch | null = null;
  let hunk: Hunk | null = null;
  for (const line of diff.split("\n")) {
    if (line.startsWith("diff --git ") || (line.startsWith("--- ") && (current === null || current.hunks.length > 0))) {
      current = { path: "", hunks: [] };
      files.push(current);
      hunk = null;
      continue;
    }
    if (!current) continue;
    if (line.startsWith("@@")) {
      hunk = { id: `${files.length - 1}:${current.hunks.length}`, header: line, lines: [], added: 0, removed: 0 };
      current.hunks.push(hunk);
    } else if (hunk) {
      if (line === "" && diff.endsWith("\n")) continue;
      hunk.lines.push(line);
      if (line.startsWith("+")) hunk.added += 1;
      else if (line.startsWith("-")) hunk.removed += 1;
    } else if (line.startsWith("+++ ")) {
      const p = line.slice(4).trim();
      current.path = p.startsWith("b/") ? p.slice(2) : p;
    } else if (line.startsWith("--- ") && !current.path) {
      const p = line.slice(4).trim();
      if (p !== "/dev/null") current.path = p.startsWith("a/") ? p.slice(2) : p;
    }
  }
  return files;
}
