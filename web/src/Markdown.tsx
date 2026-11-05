// A small markdown renderer for chat replies: code fences (diffs colored), inline code, bold,
// links, lists, headings. It builds React elements only, never HTML strings, so a model reply
// can't inject markup. An unclosed fence (mid-stream) renders as code until it closes.
import type { ReactNode } from "react";

type Block =
  | { kind: "code"; lang: string; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "para"; text: string };

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push({ kind: "para", text: para.join("\n") });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
    if (fence) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      out.push({ kind: "code", lang: fence[1].toLowerCase(), text: body.join("\n") });
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flush();
      out.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      continue;
    }
    const item = line.match(/^\s*(?:([-*])|(\d+)[.)])\s+(.*)$/);
    if (item) {
      flush();
      const ordered = Boolean(item[2]);
      const last = out[out.length - 1];
      if (last?.kind === "list" && last.ordered === ordered) last.items.push(item[3]);
      else out.push({ kind: "list", ordered, items: [item[3]] });
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    para.push(line);
  }
  flush();
  return out;
}

/** Inline: `code`, **bold**, [text](https://link). Everything else is plain text. */
export function inline(text: string, key = ""): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]+\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let n = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${n++}`;
    if (m[1]) out.push(<code key={k} className="h-icode">{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k}>{m[2].slice(2, -2)}</strong>);
    else if (m[3] && m[4]) {
      const label = m[3].slice(1, m[3].indexOf("]("));
      out.push(
        <a key={k} href={m[4]} target="_blank" rel="noreferrer noopener" style={{ color: "var(--sys)", textDecoration: "underline" }}>
          {label}
        </a>,
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const diffy = lang === "diff" || /^(diff --git|@@ |--- |\+\+\+ )/m.test(text);
  return (
    <div className="h-code">
      {lang && <span className="h-code-lang">{lang}</span>}
      <pre>
        {text.split("\n").map((line, i) => {
          const kind = !diffy
            ? ""
            : line.startsWith("+") && !line.startsWith("+++")
              ? "add"
              : line.startsWith("-") && !line.startsWith("---")
                ? "del"
                : line.startsWith("@@")
                  ? "hunk"
                  : "";
          return (
            <div key={i} className={kind ? `h-code-${kind}` : undefined}>
              {line || " "}
            </div>
          );
        })}
      </pre>
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="h-md">
      {parseBlocks(text).map((b, i) => {
        if (b.kind === "code") return <CodeBlock key={i} lang={b.lang} text={b.text} />;
        if (b.kind === "heading") {
          const size = b.level === 1 ? 19 : b.level === 2 ? 17 : 15.5;
          return (
            <p key={i} style={{ margin: 0, fontWeight: 600, fontSize: size }}>
              {inline(b.text, `h${i}`)}
            </p>
          );
        }
        if (b.kind === "list") {
          const items = b.items.map((it, j) => <li key={j}>{inline(it, `l${i}-${j}`)}</li>);
          return b.ordered ? <ol key={i}>{items}</ol> : <ul key={i}>{items}</ul>;
        }
        return (
          <p key={i} style={{ margin: 0, whiteSpace: "pre-wrap" }}>
            {inline(b.text, `p${i}`)}
          </p>
        );
      })}
    </div>
  );
}
