import clsx from "clsx";
import { BookMarked, BookmarkPlus, Check, ChevronRight, RefreshCw, Replace, Sparkles, Trash2, Wand2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getTermsDictionary, putTermsDictionary, scanEditorTerms } from "../../api";
import { Button, Checkbox, IconButton, InlineNotice, TextField } from "../../components/ui";
import { useT } from "../../i18n";
import type { EditorSegment, Term, TermOverview, TermProposal } from "../../types";
import { PanelSection } from "./controls";
import type { EditorController } from "./useEditor";
import { errorText, type Translate } from "./utils";

const KIND_LABEL: Record<Term["kind"], [string, string]> = {
  person: ["Người", "Person"],
  brand: ["Thương hiệu", "Brand"],
  organization: ["Tổ chức", "Organisation"],
  place: ["Địa danh", "Place"],
  product: ["Sản phẩm", "Product"],
  number: ["Con số", "Amount"],
  other: ["Khác", "Other"],
};

// ---- text helpers (same word rules as services/terms.py) ---------------------

const WORD = "\\p{L}\\p{N}_";
const escapeRegex = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function phraseRegex(phrase: string, caseSensitive: boolean, wholeWord = true): RegExp | null {
  const parts = phrase.trim().split(/\s+/).filter(Boolean).map(escapeRegex);
  if (!parts.length) return null;
  const body = parts.join("\\s+");
  return new RegExp(wholeWord ? `(?<![${WORD}])${body}(?![${WORD}])` : body, caseSensitive ? "gu" : "giu");
}

/** Replace matches of `find`, never inside an existing occurrence of `protect`. */
function replaceIn(text: string, find: RegExp, replacement: string, protect?: string): string {
  const guard = protect ? phraseRegex(protect, true) : null;
  const kept = guard ? [...text.matchAll(guard)].map((m) => [m.index!, m.index! + m[0].length]) : [];
  return text.replace(find, (match, ...rest) => {
    const offset = rest[rest.length - 2] as number;
    const end = offset + match.length;
    return kept.some(([a, b]) => a < end && offset < b) ? match : replacement;
  });
}

function countMatches(segments: EditorSegment[], find: RegExp | null) {
  if (!find) return { hits: 0, lines: 0 };
  let hits = 0;
  let lines = 0;
  for (const seg of segments) {
    const n = [...seg.text.matchAll(find)].length;
    hits += n;
    if (n) lines++;
  }
  return { hits, lines };
}

const plainTerm = ({ target, source, wrong, kind, note, origin }: Term): Term => ({ target, source, wrong, kind, note, origin });

/**
 * Proper names and amounts of this dub: one spelling everywhere, bulk fixes, an AI check,
 * and a dictionary shared by every future translation.
 */
export function TermsPanel({
  editor,
  jobId,
  onJump,
  L,
}: {
  editor: EditorController;
  jobId: string;
  onJump: (cueId: string, time: number) => void;
  L: Translate;
}) {
  const { segments, busy } = editor;
  const terms = useMemo(() => editor.workspace?.terms ?? [], [editor.workspace?.terms]);
  const [changedLines, setChangedLines] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const ordered = useMemo(
    () =>
      [...terms].sort((a, b) => {
        const pa = a.variants.length + a.missing_ids.length;
        const pb = b.variants.length + b.missing_ids.length;
        return pb - pa || b.uses - a.uses;
      }),
    [terms],
  );
  const problems = terms.reduce((sum, term) => sum + term.missing_ids.length + term.variants.reduce((n, v) => n + v.ids.length, 0), 0);

  /** Apply per-line rewrites; one undo step. */
  const rewrite = async (rewriteLine: (seg: EditorSegment) => string, label: string) => {
    const updates = segments
      .map((seg) => ({ seg, text: rewriteLine(seg) }))
      .filter(({ seg, text }) => text !== seg.text)
      .map(({ seg, text }) => ({ id: seg.id, text }));
    if (!updates.length) return 0;
    if (await editor.replaceTexts(updates, label)) setChangedLines((n) => n + updates.length);
    return updates.length;
  };

  const fixVariant = (term: TermOverview, variant: string) => {
    const find = phraseRegex(variant, true);
    if (!find) return;
    void rewrite((seg) => replaceIn(seg.text, find, term.target, term.target), "term-fix");
  };

  const fixAllVariants = (term: TermOverview) => {
    const finders = term.variants.map((v) => phraseRegex(v.text, true)).filter((r): r is RegExp => !!r);
    void rewrite((seg) => finders.reduce((text, find) => replaceIn(text, find, term.target, term.target), seg.text), "term-fix");
  };

  const saveSheet = (next: Term[]) => editor.saveTerms(next.map(plainTerm));

  const renameTerm = async (term: TermOverview, target: string) => {
    target = target.trim();
    if (!target || target === term.target) return;
    const old = term.target;
    const next = terms.map((item) =>
      item.target === old ? { ...plainTerm(item), target, wrong: [old, ...item.wrong.filter((w) => w !== target)], origin: "user" } : plainTerm(item),
    );
    if (!(await saveSheet(next))) return;
    const find = phraseRegex(old, true);
    if (find) await rewrite((seg) => replaceIn(seg.text, find, target, target), "term-rename");
  };

  const removeTerm = (term: TermOverview) => void saveSheet(terms.filter((item) => item.target !== term.target));

  const addTerm = async (target: string, wrong: string[]) => {
    const term: Term = { target, source: [], wrong, kind: "other", note: "", origin: "user" };
    if (!(await saveSheet([term, ...terms]))) return;
    const finders = wrong.map((w) => phraseRegex(w, false)).filter((r): r is RegExp => !!r);
    await rewrite((seg) => finders.reduce((text, find) => replaceIn(text, find, target, target), seg.text), "term-add");
  };

  const jumpTo = (ids: string[]) => {
    const seg = segments.find((item) => ids.includes(item.id));
    if (seg) onJump(seg.id, seg.start);
  };

  return (
    <>
      <PanelSection title={L("Tên riêng & con số", "Names & numbers")}>
        <p className="mb-3 text-[11.5px] leading-5 text-[var(--text-tertiary)]">
          {L(
            "Danh sách được lập khi dịch. Mỗi tên chỉ có một cách viết; sửa ở đây là sửa trong mọi câu.",
            "Built while translating. Each name has one spelling; a fix here applies to every line.",
          )}
        </p>
        <AiScan jobId={jobId} editor={editor} L={L} onApplied={(n) => setChangedLines((c) => c + n)} onError={setError} />
        {error ? (
          <InlineNotice tone="danger" className="mt-2">
            {error}
          </InlineNotice>
        ) : null}
        {changedLines > 0 ? (
          <InlineNotice
            tone="success"
            className="mt-2"
            action={
              <Button size="xs" variant="outline" leadingIcon={<RefreshCw size={13} />} disabled={!!busy || !!editor.batch} onClick={() => void editor.regenerateStale(false)}>
                {L("Tạo lại audio", "Re-voice")}
              </Button>
            }
          >
            {L(`Đã sửa ${changedLines} câu. Tạo lại giọng để audio khớp chữ mới.`, `${changedLines} lines changed. Re-voice them so the audio matches.`)}
          </InlineNotice>
        ) : null}
      </PanelSection>

      <PanelSection
        title={
          <span>
            {L("Trong video này", "In this video")} <span className="font-normal text-[var(--text-tertiary)]">({terms.length})</span>
          </span>
        }
        action={
          problems ? (
            <span className="text-[11.5px] font-medium text-[var(--warning)]">
              {L(`${problems} chỗ cần xem`, `${problems} to check`)}
            </span>
          ) : null
        }
      >
        <AddTerm onAdd={addTerm} L={L} />
        {ordered.length ? (
          <ul className="mt-3 grid gap-2">
            {ordered.map((term) => (
              <TermRow
                key={term.target}
                term={term}
                L={L}
                onRename={(target) => void renameTerm(term, target)}
                onFixVariant={(variant) => fixVariant(term, variant)}
                onFixAll={() => fixAllVariants(term)}
                onRemove={() => removeTerm(term)}
                onJump={jumpTo}
              />
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-[12px] leading-5 text-[var(--text-secondary)]">
            {L(
              "Chưa có danh sách (video dịch trước khi có tính năng này). Bấm “Dò bằng AI” để lập.",
              "No list yet (translated before this feature). Use “Check with AI” to build one.",
            )}
          </p>
        )}
      </PanelSection>

      <FindReplace segments={segments} L={L} onReplace={(fn) => rewrite(fn, "find-replace")} />
      <Dictionary terms={terms} L={L} />
    </>
  );
}

function TermRow({
  term,
  L,
  onRename,
  onFixVariant,
  onFixAll,
  onRemove,
  onJump,
}: {
  term: TermOverview;
  L: Translate;
  onRename: (target: string) => void;
  onFixVariant: (variant: string) => void;
  onFixAll: () => void;
  onRemove: () => void;
  onJump: (ids: string[]) => void;
}) {
  const [value, setValue] = useState(term.target);
  useEffect(() => setValue(term.target), [term.target]);
  const lang = useT().lang === "vi" ? 0 : 1;
  const hasIssue = term.variants.length > 0 || term.missing_ids.length > 0;
  const [saved, setSaved] = useState(false);

  const saveToDictionary = async () => {
    try {
      const { terms } = await getTermsDictionary();
      await putTermsDictionary([plainTerm({ ...term, origin: "dictionary" }), ...terms.filter((t) => t.target !== term.target)]);
      setSaved(true);
    } catch {
      setSaved(false);
    }
  };

  return (
    <li
      className={clsx(
        "rounded-[var(--radius-control)] border px-2.5 py-2",
        hasIssue ? "border-[color-mix(in_srgb,var(--warning)_45%,transparent)] bg-[color-mix(in_srgb,var(--warning)_6%,transparent)]" : "border-[var(--border-subtle)]",
      )}
    >
      <div className="flex items-center gap-1.5">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => onRename(value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            if (e.key === "Escape") setValue(term.target);
          }}
          title={L("Sửa tên đúng: đổi luôn trong mọi câu", "Edit the right spelling: changes every line")}
          className="min-w-0 flex-1 rounded-[6px] border border-transparent bg-transparent px-1 py-0.5 text-[13px] font-semibold text-[var(--text-primary)] hover:border-[var(--border)] focus:border-[var(--primary)] focus:outline-none"
        />
        <IconButton size="sm" variant="ghost" className="!h-6 !w-6" label={L("Lưu vào từ điển chung", "Save to the shared dictionary")}
          title={saved ? L("Đã lưu vào từ điển", "Saved to the dictionary") : L("Lưu vào từ điển chung (dùng cho mọi video sau)", "Save to the shared dictionary (used for future videos)")}
          onClick={() => void saveToDictionary()}
        >
          {saved || term.origin === "dictionary" ? <BookMarked size={14} className="text-[var(--primary)]" /> : <BookmarkPlus size={14} />}
        </IconButton>
        <IconButton size="sm" variant="ghost" className="!h-6 !w-6" label={L("Xoá khỏi danh sách", "Remove")} onClick={onRemove}>
          <Trash2 size={13} />
        </IconButton>
      </div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 px-1 text-[11px] text-[var(--text-tertiary)]">
        <span>{KIND_LABEL[term.kind]?.[lang] ?? term.kind}</span>
        <span>·</span>
        <span>{L(`${term.uses} câu`, `${term.uses} lines`)}</span>
        {term.note ? <span className="min-w-0 basis-full truncate" title={term.note}>{term.note}</span> : null}
      </div>
      {term.variants.length ? (
        <div className="mt-1.5 grid gap-1 px-1">
          {term.variants.map((variant) => (
            <div key={variant.text} className="flex items-center gap-1.5 text-[12px]">
              <span className="min-w-0 truncate text-[var(--danger)] line-through decoration-1" title={variant.text}>
                {variant.text}
              </span>
              <span className="shrink-0 text-[var(--text-tertiary)]">×{variant.ids.length}</span>
              <button type="button" className="ml-auto shrink-0 text-[11.5px] font-medium text-[var(--primary)] hover:underline" onClick={() => onFixVariant(variant.text)}>
                {L("Sửa", "Fix")}
              </button>
              <button type="button" className="shrink-0 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]" title={L("Xem câu", "Show line")} onClick={() => onJump(variant.ids)}>
                <ChevronRight size={13} />
              </button>
            </div>
          ))}
          {term.variants.length > 1 ? (
            <Button size="xs" variant="subtle" leadingIcon={<Wand2 size={12} />} onClick={onFixAll} className="mt-0.5 justify-self-start">
              {L(`Sửa hết thành “${term.target}”`, `Fix all to “${term.target}”`)}
            </Button>
          ) : null}
        </div>
      ) : null}
      {term.missing_ids.length ? (
        <button
          type="button"
          onClick={() => onJump(term.missing_ids)}
          className="mt-1.5 flex w-full items-center gap-1 px-1 text-left text-[11.5px] text-[var(--warning)] hover:underline"
          title={L("Câu gốc có tên này nhưng bản dịch không có. Bấm để xem.", "The source line has this name but the translation doesn't. Click to see.")}
        >
          {L(`${term.missing_ids.length} câu gốc có nhưng bản dịch thiếu`, `${term.missing_ids.length} lines miss it`)}
          <ChevronRight size={12} />
        </button>
      ) : null}
    </li>
  );
}

function AddTerm({ onAdd, L }: { onAdd: (target: string, wrong: string[]) => Promise<void>; L: Translate }) {
  const [target, setTarget] = useState("");
  const [wrong, setWrong] = useState("");
  const submit = async () => {
    const name = target.trim();
    if (!name) return;
    await onAdd(
      name,
      wrong
        .split(",")
        .map((w) => w.trim())
        .filter((w) => w && w !== name),
    );
    setTarget("");
    setWrong("");
  };
  return (
    <form
      className="grid gap-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <TextField value={target} onChange={(e) => setTarget(e.target.value)} placeholder={L("Tên đúng, vd. VinFast", "Right spelling, e.g. VinFast")} className="!min-h-8 text-[12.5px]" />
      <TextField
        value={wrong}
        onChange={(e) => setWrong(e.target.value)}
        placeholder={L("Cách viết sai, cách nhau dấu phẩy: Winfast, Vin Phát", "Wrong spellings, comma-separated")}
        className="!min-h-8 text-[12.5px]"
      />
      <Button type="submit" size="xs" variant="outline" disabled={!target.trim()} className="justify-self-start">
        {L("Thêm & sửa trong mọi câu", "Add & fix every line")}
      </Button>
    </form>
  );
}

function AiScan({
  jobId,
  editor,
  L,
  onApplied,
  onError,
}: {
  jobId: string;
  editor: EditorController;
  L: Translate;
  onApplied: (lines: number) => void;
  onError: (message: string | null) => void;
}) {
  const [running, setRunning] = useState(false);
  const [proposals, setProposals] = useState<TermProposal[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const scan = async () => {
    setRunning(true);
    onError(null);
    try {
      if (!(await editor.saveDraft())) return;
      const result = await scanEditorTerms(jobId);
      editor.adoptWorkspace(result.workspace);
      // One proposal per line: the last pass (AI after spelling) has the final text.
      const byLine = new Map<string, TermProposal>();
      for (const item of result.proposals) {
        const first = byLine.get(item.id);
        byLine.set(item.id, first ? { ...item, before: first.before } : item);
      }
      const list = [...byLine.values()];
      setProposals(list);
      setPicked(new Set(list.map((item) => item.id)));
    } catch (e) {
      onError(errorText(e));
    } finally {
      setRunning(false);
    }
  };

  const apply = async () => {
    if (!proposals) return;
    const current = new Map(editor.segments.map((seg) => [seg.id, seg.text]));
    // Skip lines edited since the scan.
    const updates = proposals.filter((p) => picked.has(p.id) && current.get(p.id) === p.before).map((p) => ({ id: p.id, text: p.after }));
    if (updates.length && (await editor.replaceTexts(updates, "term-ai"))) onApplied(updates.length);
    setProposals(null);
  };

  if (!proposals) {
    return (
      <Button size="sm" variant="secondary" leadingIcon={<Sparkles size={14} />} loading={running} disabled={running || !!editor.busy} onClick={() => void scan()} className="w-full">
        {running ? L("Đang dò tên riêng & con số…", "Checking names & numbers…") : L("Dò lại bằng AI", "Check with AI")}
      </Button>
    );
  }
  if (!proposals.length) {
    return (
      <InlineNotice tone="success" action={<Button size="xs" variant="ghost" onClick={() => setProposals(null)}>OK</Button>}>
        {L("Không thấy tên riêng hay con số nào sai.", "No wrong names or numbers found.")}
      </InlineNotice>
    );
  }
  return (
    <div className="grid gap-2">
      <div className="text-[12px] font-medium text-[var(--text-primary)]">
        {L(`AI đề xuất sửa ${proposals.length} câu`, `AI suggests ${proposals.length} fixes`)}
      </div>
      <ul className="grid max-h-[340px] gap-1.5 overflow-y-auto pr-0.5">
        {proposals.map((item) => (
          <li key={item.id} className="rounded-[var(--radius-control)] border border-[var(--border-subtle)] px-2 py-1.5">
            <Checkbox
              checked={picked.has(item.id)}
              onChange={(on) =>
                setPicked((current) => {
                  const next = new Set(current);
                  if (on) next.add(item.id);
                  else next.delete(item.id);
                  return next;
                })
              }
              label={<span className="text-[12px] font-normal leading-[1.45] text-[var(--text-primary)]">{item.after}</span>}
              description={<span className="text-[11.5px] text-[var(--text-tertiary)] line-through">{item.before}</span>}
            />
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button size="sm" variant="primary" leadingIcon={<Check size={14} />} disabled={!picked.size} onClick={() => void apply()}>
          {L(`Áp dụng ${picked.size}`, `Apply ${picked.size}`)}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setProposals(null)}>
          {L("Bỏ qua", "Dismiss")}
        </Button>
      </div>
    </div>
  );
}

function FindReplace({
  segments,
  L,
  onReplace,
}: {
  segments: EditorSegment[];
  L: Translate;
  onReplace: (rewrite: (seg: EditorSegment) => string) => Promise<number>;
}) {
  const [find, setFind] = useState("");
  const [replacement, setReplacement] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(true);
  const regex = useMemo(() => phraseRegex(find, caseSensitive, wholeWord), [find, caseSensitive, wholeWord]);
  const { hits, lines } = useMemo(() => countMatches(segments, regex), [segments, regex]);

  const run = async () => {
    if (!regex) return;
    await onReplace((seg) => seg.text.replace(regex, () => replacement));
    setFind("");
    setReplacement("");
  };

  return (
    <PanelSection title={L("Tìm & thay hàng loạt", "Find & replace")}>
      <div className="grid gap-1.5">
        <TextField value={find} onChange={(e) => setFind(e.target.value)} placeholder={L("Tìm", "Find")} className="!min-h-8 text-[12.5px]" />
        <TextField value={replacement} onChange={(e) => setReplacement(e.target.value)} placeholder={L("Thay bằng", "Replace with")} className="!min-h-8 text-[12.5px]" />
        <div className="flex flex-wrap gap-x-4 gap-y-1 py-0.5">
          <Checkbox checked={wholeWord} onChange={setWholeWord} label={<span className="text-[12px] font-normal">{L("Nguyên từ", "Whole word")}</span>} />
          <Checkbox checked={caseSensitive} onChange={setCaseSensitive} label={<span className="text-[12px] font-normal">{L("Phân biệt hoa/thường", "Match case")}</span>} />
        </div>
        <Button size="xs" variant="outline" leadingIcon={<Replace size={13} />} disabled={!hits} onClick={() => void run()} className="justify-self-start">
          {find.trim()
            ? L(`Thay ${hits} chỗ trong ${lines} câu`, `Replace ${hits} in ${lines} lines`)
            : L("Thay tất cả", "Replace all")}
        </Button>
      </div>
    </PanelSection>
  );
}

function Dictionary({ terms, L }: { terms: TermOverview[]; L: Translate }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<Term[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    getTermsDictionary()
      .then((data) => setEntries(data.terms))
      .catch((e) => setError(errorText(e)));
  }, [open, terms]);

  const remove = async (target: string) => {
    if (!entries) return;
    try {
      setEntries((await putTermsDictionary(entries.filter((entry) => entry.target !== target))).terms);
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <PanelSection
      title={L("Từ điển chung", "Shared dictionary")}
      action={
        <button type="button" className="text-[11.5px] font-medium text-[var(--primary)] hover:underline" onClick={() => setOpen((v) => !v)}>
          {open ? L("Ẩn", "Hide") : L("Xem", "Show")}
        </button>
      }
    >
      <p className="text-[11.5px] leading-5 text-[var(--text-tertiary)]">
        {L(
          "Tên lưu ở đây được dùng khi dịch mọi video sau, và cách viết sai được tự sửa.",
          "Names saved here guide every future translation; their wrong spellings are fixed automatically.",
        )}
      </p>
      {open ? (
        error ? (
          <InlineNotice tone="danger" className="mt-2">
            {error}
          </InlineNotice>
        ) : entries ? (
          entries.length ? (
            <ul className="mt-2 grid gap-1">
              {entries.map((entry) => (
                <li key={entry.target} className="flex items-center gap-2 text-[12px]">
                  <span className="min-w-0 flex-1 truncate">
                    <span className="font-medium text-[var(--text-primary)]">{entry.target}</span>
                    {entry.wrong.length ? <span className="text-[var(--text-tertiary)]"> · {L("không phải", "not")} {entry.wrong.slice(0, 3).join(", ")}</span> : null}
                  </span>
                  <IconButton size="sm" variant="ghost" className="!h-6 !w-6" label={L("Xoá", "Remove")} onClick={() => void remove(entry.target)}>
                    <Trash2 size={12} />
                  </IconButton>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-[12px] text-[var(--text-secondary)]">
              {L("Chưa có tên nào. Bấm biểu tượng dấu trang ở một tên để lưu.", "Empty. Use the bookmark icon on a name to save it.")}
            </p>
          )
        ) : null
      ) : null}
    </PanelSection>
  );
}
