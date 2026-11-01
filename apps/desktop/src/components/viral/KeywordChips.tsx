import { Plus, X } from "lucide-react";
import { useState } from "react";
import type { ViralPlatform } from "../../lib/viral-api";
import { ViralPlatformIcon } from "./shared";

/** Editable keyword chips for one platform. The first `activeCount` chips are used by scans. */
export function KeywordChips({
  platform,
  label,
  words,
  onChange,
  placeholder,
  activeCount = 3,
}: {
  platform: ViralPlatform;
  label: string;
  words: string[];
  onChange: (words: string[]) => void;
  placeholder: string;
  activeCount?: number;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const text = draft.trim().replace(/\s+/g, " ");
    if (text && !words.includes(text)) onChange([...words, text].slice(0, 8));
    setDraft("");
  };
  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center gap-1.5 text-[12.5px] font-semibold text-[var(--text-secondary)]">
        <ViralPlatformIcon platform={platform} size={14} />
        {label}
      </div>
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1.5 focus-within:border-[var(--primary)] focus-within:shadow-[var(--shadow-focus)]">
        {words.map((word, i) => (
          <span
            key={word}
            className={
              i < activeCount
                ? "inline-flex items-center gap-1 rounded-full border border-[var(--primary-soft-strong)] bg-[var(--primary-soft)] py-0.5 pl-2.5 pr-1 text-[12.5px] text-[var(--primary-ink)]"
                : "inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] py-0.5 pl-2.5 pr-1 text-[12.5px] text-[var(--text-tertiary)]"
            }
          >
            {word}
            <button
              type="button"
              aria-label={`remove ${word}`}
              onClick={() => onChange(words.filter((w) => w !== word))}
              className="rounded-full p-0.5 hover:bg-[var(--surface-hover)]"
            >
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            } else if (e.key === "Backspace" && !draft && words.length) {
              onChange(words.slice(0, -1));
            }
          }}
          onBlur={add}
          placeholder={words.length ? "" : placeholder}
          className="min-w-[120px] flex-1 bg-transparent px-1 text-[13px] outline-none placeholder:text-[var(--text-tertiary)]"
        />
        {draft ? (
          <button type="button" onClick={add} className="rounded p-0.5 text-[var(--primary)] hover:bg-[var(--primary-soft)]" aria-label="add">
            <Plus size={14} />
          </button>
        ) : null}
      </div>
    </div>
  );
}
