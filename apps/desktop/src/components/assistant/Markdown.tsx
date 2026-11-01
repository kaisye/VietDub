import clsx from "clsx";
import { Fragment, memo } from "react";
import type { ReactNode } from "react";
import { openUrl } from "../../lib/open-url";
import { CopyButton } from "./CopyButton";

/*
 * Small, safe Markdown renderer for assistant answers.
 * It parses into React elements only (never raw HTML / dangerouslySetInnerHTML),
 * so model output cannot inject markup. Supported: headings, paragraphs, line breaks,
 * ordered / unordered (nested) lists, task boxes, blockquotes, fenced code, horizontal
 * rules, GFM tables, **bold**, *italic*, ~~strike~~, `code`, [links](https://…) and bare URLs.
 * Unclosed constructs (mid-stream) degrade gracefully.
 */

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "code"; lang: string; code: string; open: boolean }
  | { type: "quote"; blocks: Block[] }
  | { type: "list"; ordered: boolean; start: number; items: Block[][] }
  | { type: "table"; header: string[]; align: Array<"left" | "center" | "right" | null>; rows: string[][] }
  | { type: "hr" };

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  let inCode = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "\\" && s[i + 1] === "|") {
      cur += "|";
      i++;
      continue;
    }
    if (ch === "`") inCode = !inCode;
    if (ch === "|" && !inCode) {
      cells.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}

function isBlockStart(line: string, next?: string): boolean {
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    HR.test(line) ||
    QUOTE.test(line) ||
    LIST_ITEM.test(line) ||
    (line.includes("|") && next !== undefined && TABLE_SEP.test(next) && next.includes("-"))
  );
}

function indentOf(line: string): number {
  const m = /^(\s*)/.exec(line);
  return m ? m[1].replace(/\t/g, "    ").length : 0;
}

export function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const code: string[] = [];
      i++;
      let open = true;
      while (i < lines.length) {
        const closing = lines[i].trim();
        if (closing.length >= marker.length && closing === marker[0].repeat(closing.length)) {
          open = false;
          i++;
          break;
        }
        code.push(lines[i]);
        i++;
      }
      blocks.push({ type: "code", lang: fence[2] || "", code: code.join("\n"), open });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }
    if (HR.test(line)) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && (QUOTE.test(lines[i]) || (lines[i].trim() && !isBlockStart(lines[i]) && inner.length))) {
        const m = QUOTE.exec(lines[i]);
        inner.push(m ? m[1] : lines[i]);
        i++;
      }
      blocks.push({ type: "quote", blocks: parseBlocks(inner.join("\n")) });
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const header = splitRow(line);
      const align = splitRow(lines[i + 1]).map((c) => {
        const l = c.startsWith(":");
        const r = c.endsWith(":");
        return l && r ? "center" : r ? "right" : l ? "left" : null;
      });
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push({ type: "table", header, align, rows });
      continue;
    }
    const item = LIST_ITEM.exec(line);
    if (item) {
      const baseIndent = indentOf(line);
      const ordered = /\d/.test(item[2]);
      const start = ordered ? parseInt(item[2], 10) : 1;
      const items: string[][] = [];
      while (i < lines.length) {
        const cur = lines[i];
        const m = LIST_ITEM.exec(cur);
        if (m && indentOf(cur) <= baseIndent + 1 && /\d/.test(m[2]) === ordered) {
          items.push([m[3]]);
          i++;
          continue;
        }
        if (m && indentOf(cur) <= baseIndent + 1) break; // different list type at same level
        if (!cur.trim()) {
          // blank line: continue the list only if the next line is indented content or another item
          const nxt = lines[i + 1];
          if (nxt !== undefined && (indentOf(nxt) > baseIndent || (LIST_ITEM.test(nxt) && indentOf(nxt) <= baseIndent + 1))) {
            items[items.length - 1].push("");
            i++;
            continue;
          }
          break;
        }
        if (indentOf(cur) > baseIndent) {
          items[items.length - 1].push(cur.slice(Math.min(indentOf(cur), baseIndent + 2)));
          i++;
          continue;
        }
        // lazy continuation of the item's paragraph
        if (!isBlockStart(cur, lines[i + 1])) {
          items[items.length - 1].push(cur.trim());
          i++;
          continue;
        }
        break;
      }
      blocks.push({ type: "list", ordered, start, items: items.map((it) => parseBlocks(it.join("\n"))) });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !(para.length && isBlockStart(lines[i], lines[i + 1]))) {
      para.push(lines[i]);
      i++;
    }
    blocks.push({ type: "paragraph", text: para.join("\n") });
  }
  return blocks;
}

// --- Inline -------------------------------------------------------------------------

const SAFE_URL = /^(https?:\/\/|mailto:)/i;

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        void openUrl(href);
      }}
      className="font-medium text-[var(--text-link,var(--primary))] underline decoration-[color-mix(in_srgb,var(--primary)_35%,transparent)] underline-offset-2 hover:decoration-[var(--primary)]"
      rel="noopener noreferrer"
    >
      {children}
    </a>
  );
}

// Order matters: code first so its content is not parsed further.
const INLINE =
  /(`+)([\s\S]*?[^`])\1(?!`)|\[([^\]\n]+)\]\(\s*<?((?:[^()\s<>]|\([^()\s]*\))+)>?(?:\s+"[^"]*")?\s*\)|\*\*([\s\S]+?)\*\*|__([\s\S]+?)__|~~([\s\S]+?)~~|\*(?!\s)([\s\S]+?)\*(?!\*)|\b_(?!\s)([\s\S]+?)_\b|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|(\n)/g;

export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  const re = new RegExp(INLINE.source, "g");
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyPrefix}-${n++}`;
    if (m[1]) {
      out.push(
        <code key={key} className="rounded-[5px] border border-[var(--border-subtle)] bg-[var(--surface-sunken)] px-1 py-px font-mono text-[0.88em] text-[var(--primary-ink)]">
          {m[2].trim() ? m[2].replace(/^ (.*) $/, "$1") : m[2]}
        </code>,
      );
    } else if (m[3] !== undefined) {
      const href = m[4];
      out.push(
        SAFE_URL.test(href) ? (
          <Link key={key} href={href}>
            {renderInline(m[3], key)}
          </Link>
        ) : (
          <Fragment key={key}>{renderInline(m[3], key)}</Fragment>
        ),
      );
    } else if (m[5] !== undefined || m[6] !== undefined) {
      out.push(
        <strong key={key} className="font-semibold text-[var(--text-primary)]">
          {renderInline(m[5] ?? m[6], key)}
        </strong>,
      );
    } else if (m[7] !== undefined) {
      out.push(<del key={key}>{renderInline(m[7], key)}</del>);
    } else if (m[8] !== undefined || m[9] !== undefined) {
      out.push(<em key={key}>{renderInline(m[8] ?? m[9], key)}</em>);
    } else if (m[10]) {
      out.push(
        <Link key={key} href={m[10]}>
          {m[10]}
        </Link>,
      );
    } else if (m[11]) {
      out.push(<br key={key} />);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// --- Blocks -------------------------------------------------------------------------

const headingClass: Record<number, string> = {
  1: "text-[18px] font-semibold tracking-[-0.01em] mt-5 mb-2",
  2: "text-[16px] font-semibold tracking-[-0.01em] mt-5 mb-2",
  3: "text-[14.5px] font-semibold mt-4 mb-1.5",
  4: "text-[14px] font-semibold mt-3 mb-1",
  5: "text-[13.5px] font-semibold mt-3 mb-1",
  6: "text-[13px] font-semibold uppercase tracking-[0.04em] text-[var(--text-secondary)] mt-3 mb-1",
};

function TaskOrText({ text, k }: { text: string; k: string }) {
  const task = /^\[([ xX])\]\s+(.*)$/s.exec(text);
  if (!task) return <>{renderInline(text, k)}</>;
  return (
    <span className="inline-flex items-start gap-2">
      <input type="checkbox" checked={task[1] !== " "} readOnly className="mt-[5px] h-3.5 w-3.5 accent-[var(--primary)]" />
      <span>{renderInline(task[2], k)}</span>
    </span>
  );
}

function BlockView({ block, k, copyLabel, copiedLabel }: { block: Block; k: string; copyLabel: string; copiedLabel: string }) {
  switch (block.type) {
    case "heading": {
      const Tag = `h${Math.min(block.level + 1, 6)}` as "h2";
      return <Tag className={clsx("text-[var(--text-primary)] first:mt-0", headingClass[block.level])}>{renderInline(block.text, k)}</Tag>;
    }
    case "paragraph":
      return <p className="my-2.5 first:mt-0 last:mb-0">{renderInline(block.text, k)}</p>;
    case "hr":
      return <hr className="my-4 border-[var(--border)]" />;
    case "code":
      return (
        <div className="group/code my-3 overflow-hidden rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] first:mt-0 last:mb-0">
          <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-1">
            <span className="font-mono text-[11px] text-[var(--text-tertiary)]">{block.lang || "text"}</span>
            <CopyButton text={block.code} label={copyLabel} copiedLabel={copiedLabel} size="xs" />
          </div>
          <pre className="overflow-x-auto px-3.5 py-3 font-mono text-[12.5px] leading-5 text-[var(--text-primary)]">
            <code>{block.code}</code>
          </pre>
        </div>
      );
    case "quote":
      return (
        <blockquote className="my-3 border-l-[3px] border-[var(--primary-soft-strong)] pl-3.5 text-[var(--text-secondary)] first:mt-0 last:mb-0">
          {block.blocks.map((b, i) => (
            <BlockView key={i} block={b} k={`${k}-${i}`} copyLabel={copyLabel} copiedLabel={copiedLabel} />
          ))}
        </blockquote>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag
          start={block.ordered && block.start !== 1 ? block.start : undefined}
          className={clsx(
            "my-2.5 grid gap-1 pl-5 first:mt-0 last:mb-0 marker:text-[var(--text-tertiary)]",
            block.ordered ? "list-decimal" : "list-disc",
          )}
        >
          {block.items.map((item, i) => (
            <li key={i} className="pl-1">
              {item.map((b, j) =>
                b.type === "paragraph" && j === 0 ? (
                  <TaskOrText key={j} text={b.text} k={`${k}-${i}-${j}`} />
                ) : (
                  <BlockView key={j} block={b} k={`${k}-${i}-${j}`} copyLabel={copyLabel} copiedLabel={copiedLabel} />
                ),
              )}
            </li>
          ))}
        </Tag>
      );
    }
    case "table":
      return (
        <div className="my-3 overflow-x-auto rounded-[var(--radius-panel-sm)] border border-[var(--border)] first:mt-0 last:mb-0">
          <table className="w-full border-collapse text-[13px]">
            <thead className="bg-[var(--surface-muted)]">
              <tr>
                {block.header.map((cell, i) => (
                  <th
                    key={i}
                    style={{ textAlign: block.align[i] ?? "left" }}
                    className="border-b border-[var(--border)] px-3 py-2 font-semibold text-[var(--text-primary)]"
                  >
                    {renderInline(cell, `${k}-h${i}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r} className="border-b border-[var(--border-subtle)] last:border-0">
                  {block.header.map((_, c) => (
                    <td key={c} style={{ textAlign: block.align[c] ?? "left" }} className="px-3 py-2 align-top">
                      {renderInline(row[c] ?? "", `${k}-${r}-${c}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export const Markdown = memo(function Markdown({
  source,
  className,
  copyLabel = "Copy",
  copiedLabel = "Copied",
}: {
  source: string;
  className?: string;
  copyLabel?: string;
  copiedLabel?: string;
}) {
  const blocks = parseBlocks(source);
  return (
    <div className={clsx("break-words text-[14px] leading-[1.7] text-[var(--text-primary)]", className)}>
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} k={`b${i}`} copyLabel={copyLabel} copiedLabel={copiedLabel} />
      ))}
    </div>
  );
});
