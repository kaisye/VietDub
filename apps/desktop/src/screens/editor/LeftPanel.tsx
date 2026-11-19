import clsx from "clsx";
import { AudioLines, BookA, ListVideo, Music2, Palette, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { memo, useEffect, useMemo, useRef } from "react";
import { JobContentGenerator } from "../../components/assistant";
import { Button, ProgressBar, SearchInput, Select, Switch } from "../../components/ui";
import { useT } from "../../i18n";
import type { EditorBlur, EditorIssue, EditorSegment, EditorWorkspace } from "../../types";
import StylePanel from "../EditorStyle";
import { PanelSection, RangeField } from "./controls";
import { ReviewPanel } from "./ReviewPanel";
import { TermsPanel } from "./TermsPanel";
import { MusicPanel } from "./music/MusicPanel";
import type { AudioTracksController } from "./music/useAudioTracks";
import type { AutoReviewController } from "./useAutoReview";
import type { EditorController, SelectMode } from "./useEditor";
import { formatTime, ISSUE_LABEL, type Translate } from "./utils";

export type LeftTab = "cues" | "voices" | "names" | "music" | "styles" | "ai" | "review";
export type CueFilter = "all" | "issues" | EditorIssue;

const ISSUE_DOT: Record<EditorIssue, string> = {
  overrun: "bg-[var(--danger)]",
  untranslated: "bg-[var(--danger)]",
  empty: "bg-[var(--text-tertiary)]",
  stale_audio: "bg-[var(--warning)]",
  no_audio: "bg-[var(--accent-violet)]",
  term: "bg-[var(--warning)]",
};

/** Left media / assets panel: cues, voices, styles and the AI content generator. */
export function LeftPanel({
  tab,
  onTab,
  editor,
  activeId,
  filter,
  onFilter,
  query,
  onQuery,
  jobId,
  onOpenAssistant,
  review,
  onReviewJump,
  onReviewFocus,
  music,
  getPlayhead,
  pausePreview,
  L,
}: {
  tab: LeftTab;
  onTab: (tab: LeftTab) => void;
  editor: EditorController;
  activeId: string | null;
  filter: CueFilter;
  onFilter: (filter: CueFilter) => void;
  query: string;
  onQuery: (query: string) => void;
  jobId: string;
  onOpenAssistant?: () => void;
  review: AutoReviewController;
  onReviewJump: (cueId: string, time: number) => void;
  onReviewFocus: (range: { start: number; end: number } | null) => void;
  music: AudioTracksController;
  getPlayhead: () => number;
  pausePreview: () => void;
  L: Translate;
}) {
  const { t } = useT();
  const workspace = editor.workspace!;
  // Badge on the review tab: fixes waiting for approval, else open issues.
  const reviewCount = review.pending || review.review?.summary_current?.issues || 0;
  const termIssues = editor.segments.filter((seg) => seg.issues.includes("term")).length;
  const tabs: { id: LeftTab; label: string; icon: React.ReactNode; count?: number }[] = [
    { id: "cues", label: t.ed_tab_cues, icon: <ListVideo size={15} /> },
    { id: "voices", label: t.ed_tab_voices, icon: <AudioLines size={15} /> },
    { id: "names", label: L("Tên", "Names"), icon: <BookA size={15} />, count: termIssues },
    { id: "music", label: t.mu_tab, icon: <Music2 size={15} /> },
    { id: "styles", label: t.ed_tab_styles, icon: <Palette size={15} /> },
    { id: "ai", label: t.ed_tab_ai, icon: <Sparkles size={15} /> },
    { id: "review", label: t.ar_tab, icon: <ShieldCheck size={15} />, count: reviewCount },
  ];
  return (
    <aside
      className={clsx(
        "flex min-h-0 shrink-0 flex-col border-r border-[var(--border)] bg-[var(--surface)] transition-[width] duration-150",
        tab === "review" ? "w-[360px]" : tab === "music" || tab === "names" ? "w-[330px]" : "w-[280px]",
      )}
    >
      <div role="tablist" className="grid shrink-0 grid-cols-7 border-b border-[var(--border)] px-0.5 pt-1.5">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => onTab(item.id)}
            className={clsx(
              "relative -mb-px flex min-w-0 flex-col items-center gap-1 whitespace-nowrap border-b-2 pb-2 pt-1.5 text-[10.5px] font-medium tracking-[-0.01em] transition-colors",
              tab === item.id
                ? "border-[var(--primary)] text-[var(--primary-hover)]"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            {item.icon}
            {item.label}
            {item.count ? (
              <span
                className={clsx(
                  "absolute right-1 top-0.5 min-w-[17px] rounded-full px-1 text-center text-[9.5px] font-semibold leading-[15px] tabular-nums",
                  (item.id === "review" ? review.pending : item.id === "names")
                    ? "bg-[var(--warning)] text-white"
                    : "bg-[var(--surface-sunken)] text-[var(--text-secondary)]",
                )}
              >
                {item.count > 99 ? "99+" : item.count}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "cues" ? (
          <CuesTab editor={editor} activeId={activeId} filter={filter} onFilter={onFilter} query={query} onQuery={onQuery} L={L} />
        ) : tab === "voices" ? (
          <VoicesTab editor={editor} L={L} />
        ) : tab === "names" ? (
          <TermsPanel editor={editor} jobId={jobId} onJump={onReviewJump} L={L} />
        ) : tab === "music" ? (
          <MusicPanel music={music} jobId={jobId} getPlayhead={getPlayhead} pausePreview={pausePreview} />
        ) : tab === "styles" ? (
          <>
            <PanelSection title={t.ed_subtitle_style}>
              <StylePanel
                style={workspace.style}
                blur={workspace.blur}
                maxLines={workspace.max_lines}
                onChange={editor.saveStyle}
                onMaxLines={editor.saveMaxLines}
                L={L}
              />
            </PanelSection>
            <BlurSection blur={workspace.blur} onChange={editor.saveBlur} L={L} />
          </>
        ) : tab === "review" ? (
          <ReviewPanel jobId={jobId} review={review} onJump={onReviewJump} onFocus={onReviewFocus} />
        ) : (
          <div className="p-3">
            <JobContentGenerator jobId={jobId} compact onOpenSettings={onOpenAssistant} />
          </div>
        )}
      </div>
    </aside>
  );
}

function CuesTab({
  editor,
  activeId,
  filter,
  onFilter,
  query,
  onQuery,
  L,
}: {
  editor: EditorController;
  activeId: string | null;
  filter: CueFilter;
  onFilter: (filter: CueFilter) => void;
  query: string;
  onQuery: (query: string) => void;
  L: Translate;
}) {
  const { t, lang } = useT();
  const { segments } = editor;
  const issueCounts = useMemo(() => {
    const counts: Partial<Record<EditorIssue, number>> = {};
    let any = 0;
    for (const seg of segments) {
      if (seg.issues.length) any++;
      for (const issue of seg.issues) counts[issue] = (counts[issue] ?? 0) + 1;
    }
    return { any, counts };
  }, [segments]);
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return segments.filter((seg) => {
      if (filter === "issues" && !seg.issues.length) return false;
      if (filter !== "all" && filter !== "issues" && !seg.issues.includes(filter)) return false;
      if (q && !seg.text.toLowerCase().includes(q) && !seg.source_text.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [segments, filter, query]);

  return (
    <div className="flex min-h-full flex-col">
      <div className="sticky top-0 z-[1] grid gap-2 border-b border-[var(--border-subtle)] bg-[var(--surface)] p-3">
        <SearchInput placeholder={L("Tìm câu…", "Search lines…")} value={query} onChange={(e) => onQuery(e.target.value)} />
        <div className="flex items-center gap-2">
          <Select value={filter} onChange={(e) => onFilter(e.target.value as CueFilter)} className="!min-h-8 flex-1 text-[12.5px]">
            <option value="all">{L("Tất cả", "All")} ({segments.length})</option>
            <option value="issues">{L("Có vấn đề", "With issues")} ({issueCounts.any})</option>
            {(Object.keys(ISSUE_LABEL) as EditorIssue[]).map((issue) =>
              issueCounts.counts[issue] ? (
                <option key={issue} value={issue}>
                  {ISSUE_LABEL[issue][lang === "vi" ? 0 : 1]} ({issueCounts.counts[issue]})
                </option>
              ) : null,
            )}
          </Select>
          <Button size="xs" variant="ghost" onClick={() => editor.nextIssue(1)} title={t.ed_next_issue}>
            {t.ed_next_issue}
          </Button>
        </div>
      </div>
      <CueList
        segments={visible}
        selectedIds={editor.selectedIds}
        primaryId={editor.selectedId}
        activeId={activeId}
        lang={lang}
        onSelect={editor.select}
      />
    </div>
  );
}

const CueList = memo(function CueList({
  segments,
  selectedIds,
  primaryId,
  activeId,
  lang,
  onSelect,
}: {
  segments: EditorSegment[];
  selectedIds: Set<string>;
  primaryId: string | null;
  activeId: string | null;
  lang: string;
  onSelect: (seg: EditorSegment, options?: { jump?: boolean; mode?: SelectMode }) => void;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-id="${primaryId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [primaryId]);
  if (!segments.length) return <p className="p-4 text-[12.5px] text-[var(--text-tertiary)]">—</p>;
  return (
    <div ref={listRef} className="py-1">
      {segments.map((seg) => {
        const isSelected = selectedIds.has(seg.id);
        return (
          <button
            key={seg.id}
            type="button"
            data-id={seg.id}
            onClick={(event) =>
              onSelect(seg, { mode: event.shiftKey ? "range" : event.metaKey || event.ctrlKey ? "toggle" : "replace" })
            }
            className={clsx(
              "grid w-full grid-cols-[52px_1fr_auto] items-start gap-2 border-l-2 px-3 py-2 text-left text-[12.5px] transition-colors",
              isSelected
                ? "border-[var(--primary)] bg-[var(--primary-soft)]"
                : seg.id === activeId
                  ? "border-[var(--primary-soft-strong)] bg-[var(--surface-muted)]"
                  : "border-transparent hover:bg-[var(--surface-hover)]",
            )}
          >
            <span className="pt-px font-mono text-[11px] text-[var(--text-tertiary)]">{formatTime(seg.start)}</span>
            <span className={clsx("line-clamp-2 leading-[1.45]", seg.muted ? "text-[var(--text-tertiary)] line-through" : "text-[var(--text-primary)]")}>
              {seg.text || "—"}
            </span>
            <span className="flex items-center gap-1 pt-1">
              {seg.audio_url?.includes("/editor/") ? <RefreshCw size={11} className="text-[var(--accent-cyan)]" /> : null}
              {seg.issues.map((issue) => (
                <span
                  key={issue}
                  className={clsx("h-2 w-2 rounded-full", ISSUE_DOT[issue])}
                  title={ISSUE_LABEL[issue][lang === "vi" ? 0 : 1]}
                />
              ))}
            </span>
          </button>
        );
      })}
    </div>
  );
});

/** Dub mix and batch re-voicing. */
function VoicesTab({ editor, L }: { editor: EditorController; L: Translate }) {
  const { t } = useT();
  const workspace = editor.workspace as EditorWorkspace;
  const { segments, batch, busy } = editor;
  const stale = segments.filter((seg) => seg.issues.includes("stale_audio") || seg.issues.includes("no_audio")).length;
  const voiced = segments.filter((seg) => seg.audio_url).length;
  const regenerated = segments.filter((seg) => seg.audio_url?.includes("/editor/")).length;
  const overrun = segments.filter((seg) => seg.issues.includes("overrun")).length;
  // Workspaces saved before the switches existed have no flag: both are on.
  const audio = { ...workspace.audio, dubbed_enabled: workspace.audio.dubbed_enabled ?? true, original_enabled: workspace.audio.original_enabled ?? true };
  return (
    <>
      <PanelSection title={t.ed_mix}>
        <div className="grid gap-3">
          <Switch
            checked={audio.dubbed_enabled}
            onChange={(dubbed_enabled) => editor.saveAudio({ dubbed_enabled })}
            label={L("Giọng lồng", "Dub voice")}
          />
          <RangeField
            label={L("Âm lượng giọng lồng", "Dub voice volume")}
            value={audio.dubbed_volume}
            min={0}
            max={200}
            format={(v) => `${Math.round(v)}%`}
            disabled={!audio.dubbed_enabled}
            onCommit={(dubbed_volume) => editor.saveAudio({ dubbed_volume })}
          />
          <Switch
            checked={audio.original_enabled}
            onChange={(original_enabled) => editor.saveAudio({ original_enabled })}
            label={L("Âm gốc", "Original audio")}
          />
          <RangeField
            label={L("Âm lượng âm gốc", "Original audio volume")}
            value={audio.original_volume}
            min={0}
            max={200}
            format={(v) => `${Math.round(v)}%`}
            disabled={!audio.original_enabled}
            title={L("Xem trước tối đa 100%; mức cao hơn chỉ nghe được trong bản render.", "Preview caps at 100%; higher levels are heard in the render.")}
            onCommit={(original_volume) => editor.saveAudio({ original_volume })}
          />
          <p className="text-[11.5px] leading-5 text-[var(--text-tertiary)]">{t.ed_regions_hint}</p>
        </div>
      </PanelSection>
      <PanelSection title={t.ed_voice_lines}>
        <div className="grid grid-cols-2 gap-2 text-[12px]">
          <Stat label={t.ed_voiced} value={`${voiced}/${segments.length}`} />
          <Stat label={t.ed_regenerated_count} value={regenerated} />
          <Stat label={L("Audio cũ / thiếu", "Stale / missing")} value={stale} tone={stale ? "warning" : undefined} />
          <Stat label={L("Audio tràn", "Overrun")} value={overrun} tone={overrun ? "danger" : undefined} />
        </div>
        <div className="mt-3 grid gap-2">
          {batch ? (
            <>
              <ProgressBar value={batch.done} max={batch.total} />
              <div className="flex items-center justify-between text-[12px] text-[var(--text-secondary)]">
                <span>
                  {L("Đang tạo lại audio", "Regenerating audio")} {batch.done}/{batch.total}
                </span>
                <Button size="xs" variant="ghost" onClick={editor.cancelBatch}>
                  {L("Dừng", "Stop")}
                </Button>
              </div>
            </>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                leadingIcon={<RefreshCw size={14} />}
                disabled={!stale || !!busy}
                onClick={() => void editor.regenerateStale(false)}
              >
                {L(`Tạo lại audio cho ${stale} câu đã sửa`, `Regenerate ${stale} edited lines`)}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={editor.selectedIds.size < 1 || !!busy}
                onClick={() => void editor.regenerateStale(true)}
              >
                {t.ed_regen_selected} ({editor.selectedIds.size})
              </Button>
            </>
          )}
        </div>
      </PanelSection>
    </>
  );
}

function Stat({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "warning" | "danger" }) {
  return (
    <div className="rounded-[var(--radius-control)] border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-2.5 py-2">
      <div className="text-[11px] text-[var(--text-secondary)]">{label}</div>
      <div
        className={clsx(
          "mt-0.5 font-semibold tabular-nums",
          tone === "warning" ? "text-[var(--warning)]" : tone === "danger" ? "text-[var(--danger)]" : "text-[var(--text-primary)]",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function BlurSection({ blur, onChange, L }: { blur: EditorBlur; onChange: (blur: Partial<EditorBlur>) => void; L: Translate }) {
  return (
    <PanelSection title={L("Làm mờ sub gốc", "Blur hard subs")}>
      <div className="grid gap-3" id="editor-blur-section">
        <Switch checked={blur.enabled} onChange={(enabled) => onChange({ enabled })} label={L("Bật làm mờ", "Blur on")} />
        <label className="grid gap-1.5 text-[12px] font-medium text-[var(--text-secondary)]">
          {L("Kiểu", "Style")}
          <Select
            value={blur.style}
            disabled={!blur.enabled}
            onChange={(e) => onChange({ style: e.target.value as EditorBlur["style"] })}
            className="!min-h-8 text-[12.5px]"
          >
            <option value="box">{L("Đều", "Box")}</option>
            <option value="horizontal">{L("Ngang", "Horizontal")}</option>
            <option value="vertical">{L("Dọc", "Vertical")}</option>
          </Select>
        </label>
        <RangeField
          label={L("Độ mờ", "Strength")}
          value={blur.strength}
          min={0}
          max={250}
          disabled={!blur.enabled}
          onCommit={(strength) => onChange({ strength })}
        />
        <p className="text-[11.5px] leading-5 text-[var(--text-tertiary)]">
          {L("Kéo khung trên video để di chuyển, góc phải dưới để đổi cỡ.", "Drag the box on the video; bottom-right corner resizes.")}
        </p>
      </div>
    </PanelSection>
  );
}
