import {
  AlignStartVertical,
  ArrowLeftRight,
  Combine,
  Languages,
  MousePointerClick,
  Play,
  RefreshCw,
  Scissors,
  Trash2,
  Volume2,
  VolumeX,
} from "lucide-react";
import { Badge, Button, EmptyState } from "../../components/ui";
import { useT } from "../../i18n";
import type { EditorRegion, EditorWorkspace } from "../../types";
import { InfoRow, Kbd, PanelSection, RangeField, TimeField } from "./controls";
import type { EditorController } from "./useEditor";
import type { Playback } from "./usePlayback";
import { ClipInspector, MultiClipInspector } from "./music/ClipInspector";
import { TextInspector } from "./text/TextInspector";
import type { AudioTracksController } from "./music/useAudioTracks";
import { formatTime, ISSUE_LABEL, round3, type Translate } from "./utils";

/** Right inspector: whatever is selected — one cue, several cues, an original-audio region, or the project. */
export function Inspector({
  editor,
  playback,
  music,
  getPlayhead,
  L,
}: {
  editor: EditorController;
  playback: Playback;
  music: AudioTracksController;
  getPlayhead: () => number;
  L: Translate;
}) {
  const { t } = useT();
  const workspace = editor.workspace as EditorWorkspace;
  const region = workspace.original_regions.find((item) => item.id === editor.selectedRegionId) ?? null;
  let body: React.ReactNode;
  let heading: string;
  if (editor.textSelection) {
    heading = t.tx_inspector;
    body = <TextInspector editor={editor} />;
  } else if (editor.clipSelection.length === 1) {
    heading = t.mu_inspector_clip;
    body = <ClipInspector editor={editor} playback={playback} music={music} getPlayhead={getPlayhead} />;
  } else if (editor.clipSelection.length > 1) {
    heading = `${editor.clipSelection.length} ${t.mu_clips_selected}`;
    body = <MultiClipInspector editor={editor} music={music} />;
  } else if (region) {
    heading = t.ed_inspector_region;
    body = <RegionInspector editor={editor} playback={playback} region={region} L={L} />;
  } else if (editor.selectedIds.size > 1) {
    heading = `${editor.selectedIds.size} ${t.ed_cues_selected}`;
    body = <MultiInspector editor={editor} L={L} />;
  } else if (editor.selected && editor.draft) {
    heading = `${t.ed_inspector_cue} #${editor.selectedIndex + 1}`;
    body = <CueInspector editor={editor} playback={playback} L={L} />;
  } else {
    heading = t.ed_inspector_project;
    body = <ProjectInspector workspace={workspace} L={L} />;
  }
  return (
    <aside className="flex min-h-0 w-[300px] shrink-0 flex-col border-l border-[var(--border)] bg-[var(--surface)]">
      <div className="flex h-11 shrink-0 items-center border-b border-[var(--border)] px-4 text-[13px] font-semibold">
        {heading}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{body}</div>
    </aside>
  );
}

function CueInspector({ editor, playback, L }: { editor: EditorController; playback: Playback; L: Translate }) {
  const { t, lang } = useT();
  const selected = editor.selected!;
  const draft = editor.draft!;
  const { busy, batch, segments, selectedIndex } = editor;
  const tempo = selected.voice_slot && selected.audio_source_duration ? selected.audio_source_duration / selected.audio_duration : null;
  const regenerated = !!selected.audio_url && !!selected.audio_generated_at && selected.audio_url.includes("/editor/");
  return (
    <>
      <PanelSection
        title={t.ed_text}
        action={
          <span className="flex gap-1">
            <Button size="xs" variant="ghost" onClick={() => editor.nextIssue(-1)} title={t.ed_prev_issue}>
              ↑
            </Button>
            <Button size="xs" variant="ghost" onClick={() => editor.nextIssue(1)} title={t.ed_next_issue}>
              ↓
            </Button>
          </span>
        }
      >
        {selected.issues.length ? (
          <div className="mb-2 flex flex-wrap gap-1">
            {selected.issues.map((issue) => (
              <Badge
                key={issue}
                size="sm"
                tone={issue === "overrun" || issue === "untranslated" ? "danger" : issue === "stale_audio" ? "warning" : "neutral"}
              >
                {ISSUE_LABEL[issue][lang === "vi" ? 0 : 1]}
              </Badge>
            ))}
          </div>
        ) : null}
        {selected.source_text ? (
          <p className="mb-2 rounded-[var(--radius-control)] bg-[var(--surface-muted)] px-2.5 py-2 text-[12px] leading-5 text-[var(--text-secondary)]">
            {selected.source_text}
          </p>
        ) : null}
        <textarea
          ref={editor.textRef}
          rows={3}
          value={draft.text}
          className="w-full resize-y rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-[13.5px] leading-6 text-[var(--text-primary)] outline-none transition focus:border-[var(--primary)] focus:shadow-[var(--shadow-focus)]"
          onChange={(e) => editor.setDraft({ ...draft, text: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void editor.saveDraft();
            }
            if (e.key === "Escape") (e.target as HTMLTextAreaElement).blur();
          }}
        />
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button size="xs" leadingIcon={<Play size={12} />} onClick={() => playback.playSegment(selected)}>
            {L("Nghe", "Play")}
          </Button>
          <Button
            size="xs"
            leadingIcon={<Languages size={12} />}
            loading={busy === "translate"}
            disabled={!!busy || !selected.source_text}
            onClick={() => void editor.retranslate()}
          >
            {L("Dịch lại", "Retranslate")}
          </Button>
          <Button
            size="xs"
            leadingIcon={<Scissors size={12} />}
            loading={busy === "split"}
            disabled={!!busy}
            title={L(
              "Tách thành 2 lần hiển thị tại vị trí con trỏ (không đặt con trỏ: tự tách ở dấu phẩy gần giữa câu). Giọng đọc giữ nguyên.",
              "Split into two cues at the caret (no caret: the clause break nearest the middle). The voice is kept.",
            )}
            onClick={() => void editor.splitSelected()}
          >
            {L("Tách câu", "Split")}
          </Button>
          <Button
            size="xs"
            leadingIcon={<Combine size={12} />}
            loading={busy === "merge"}
            disabled={!!busy || selectedIndex >= segments.length - 1}
            onClick={() => void editor.mergeSelected()}
          >
            {L("Gộp câu sau", "Merge next")}
          </Button>
        </div>
      </PanelSection>

      <PanelSection title={t.ed_timing}>
        <div className="grid grid-cols-2 gap-2">
          <TimeField label={L("Bắt đầu", "Start")} value={draft.start} onChange={(start) => editor.setDraft({ ...draft, start })} />
          <TimeField label={L("Kết thúc", "End")} value={draft.end} onChange={(end) => editor.setDraft({ ...draft, end })} />
        </div>
        <InfoRow label={t.ed_slot}>{(selected.end - selected.start).toFixed(2)}s</InfoRow>
      </PanelSection>

      <PanelSection title={t.ed_voice}>
        {selected.audio_url ? (
          <>
            <InfoRow label={t.ed_voice_at}>
              {formatTime(selected.audio_start)} → {formatTime(selected.audio_start + selected.audio_duration)}
            </InfoRow>
            <InfoRow label={t.ed_length}>{selected.audio_duration.toFixed(2)}s</InfoRow>
            <InfoRow label={t.ed_speed_label}>{tempo ? `×${tempo.toFixed(2)}` : "×1.00"}</InfoRow>
            <InfoRow label={t.ed_take}>
              {regenerated
                ? L(
                    `tạo lại ${new Date(selected.audio_generated_at!).toLocaleTimeString()}`,
                    `regenerated ${new Date(selected.audio_generated_at!).toLocaleTimeString()}`,
                  )
                : L("bản gốc từ pipeline", "original pipeline take")}
            </InfoRow>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Button size="xs" leadingIcon={<Play size={12} />} onClick={() => playback.previewTake(selected.audio_url)}>
                {t.ed_play_take}
              </Button>
              {Math.abs(selected.audio_start - selected.start) > 0.01 ? (
                <Button
                  size="xs"
                  leadingIcon={<AlignStartVertical size={12} />}
                  onClick={() => void editor.retimeVoice(selected.id, { audio_start: selected.start })}
                >
                  {L("Đưa giọng về đầu câu", "Align to cue")}
                </Button>
              ) : null}
              {tempo ? (
                <Button
                  size="xs"
                  onClick={() => void editor.retimeVoice(selected.id, { audio_length: selected.audio_source_duration })}
                >
                  {L("Về tốc độ gốc", "Natural speed")}
                </Button>
              ) : null}
            </div>
          </>
        ) : (
          <p className="text-[12.5px] text-[var(--text-tertiary)]">{L("Câu này chưa có audio.", "This line has no audio yet.")}</p>
        )}
        {selected.previous_audio_url ? (
          <div className="mt-3 flex items-center gap-1.5 rounded-[var(--radius-control)] border border-dashed border-[var(--border-strong)] px-2.5 py-2 text-[12px]">
            <span className="flex-1 text-[var(--text-secondary)]">
              {L("Bản trước", "Previous take")} · {(selected.previous_audio_duration ?? 0).toFixed(2)}s
            </span>
            <Button size="xs" variant="ghost" onClick={() => playback.previewTake(selected.previous_audio_url)}>
              <Play size={12} />
            </Button>
            <Button size="xs" leadingIcon={<ArrowLeftRight size={12} />} disabled={!!busy} onClick={() => void editor.swapAudio()}>
              {L("Dùng bản này", "Use this")}
            </Button>
          </div>
        ) : null}
        <div className="mt-3 grid grid-cols-2 gap-1.5">
          <Button
            size="sm"
            variant="primary"
            leadingIcon={<RefreshCw size={14} />}
            loading={busy === "tts"}
            disabled={!!busy || !!batch}
            onClick={() => void editor.regenerateAudio()}
          >
            {selected.audio_url ? L("Tạo lại audio", "Regenerate") : L("Tạo audio", "Generate")}
          </Button>
          <Button
            size="sm"
            leadingIcon={selected.muted ? <Volume2 size={14} /> : <VolumeX size={14} />}
            disabled={!!busy}
            onClick={() => void editor.toggleMute()}
          >
            {selected.muted ? L("Bật tiếng", "Unmute") : L("Tắt tiếng", "Mute")}
          </Button>
        </div>
      </PanelSection>

      <PanelSection title={t.ed_danger_zone}>
        <Button
          size="sm"
          variant="danger"
          block
          leadingIcon={<Trash2 size={14} />}
          loading={busy === "delete"}
          disabled={!!busy}
          onClick={() => void editor.deleteSelection()}
        >
          {L("Xóa cue", "Delete cue")} <Kbd>Del</Kbd>
        </Button>
      </PanelSection>
    </>
  );
}

function MultiInspector({ editor, L }: { editor: EditorController; L: Translate }) {
  const { t } = useT();
  const chosen = editor.segments.filter((seg) => editor.selectedIds.has(seg.id));
  const span = chosen.length ? chosen[chosen.length - 1].end - chosen[0].start : 0;
  const allMuted = chosen.every((seg) => seg.muted);
  const misaligned = chosen.filter((seg) => seg.audio_url && Math.abs(seg.audio_start - seg.start) > 0.01);
  return (
    <>
      <PanelSection title={t.ed_selection}>
        <InfoRow label={t.ed_range}>
          {chosen.length ? `${formatTime(chosen[0].start)} → ${formatTime(chosen[chosen.length - 1].end)}` : "—"}
        </InfoRow>
        <InfoRow label={t.ed_length}>{span.toFixed(2)}s</InfoRow>
        <InfoRow label={t.ed_voiced}>{chosen.filter((seg) => seg.audio_url).length}</InfoRow>
        <p className="mt-2 text-[11.5px] leading-5 text-[var(--text-tertiary)]">{t.ed_multi_hint}</p>
      </PanelSection>
      <PanelSection title={t.ed_actions}>
        <div className="grid gap-1.5">
          <Button
            size="sm"
            leadingIcon={<RefreshCw size={14} />}
            disabled={!!editor.busy || !!editor.batch}
            onClick={() => void editor.regenerateStale(true)}
          >
            {t.ed_regen_selected}
          </Button>
          <Button
            size="sm"
            leadingIcon={allMuted ? <Volume2 size={14} /> : <VolumeX size={14} />}
            disabled={!!editor.busy}
            onClick={() => void editor.toggleMute()}
          >
            {allMuted ? L("Bật tiếng", "Unmute") : L("Tắt tiếng", "Mute")}
          </Button>
          <Button
            size="sm"
            leadingIcon={<AlignStartVertical size={14} />}
            disabled={!misaligned.length || !!editor.busy}
            onClick={() =>
              void editor.moveSegments(misaligned.map((seg) => ({ id: seg.id, audio_start: round3(seg.start) })))
            }
          >
            {t.ed_align_voices} ({misaligned.length})
          </Button>
          <Button
            size="sm"
            variant="danger"
            leadingIcon={<Trash2 size={14} />}
            loading={editor.busy === "delete"}
            disabled={!!editor.busy}
            onClick={() => void editor.deleteSelection()}
          >
            {t.ed_delete_n.replace("{n}", String(chosen.length))}
          </Button>
        </div>
      </PanelSection>
    </>
  );
}

function RegionInspector({
  editor,
  playback,
  region,
  L,
}: {
  editor: EditorController;
  playback: Playback;
  region: EditorRegion;
  L: Translate;
}) {
  const workspace = editor.workspace as EditorWorkspace;
  const index = workspace.original_regions.indexOf(region) + 1;
  const update = (patch: Partial<EditorRegion>) =>
    editor.saveRegions(workspace.original_regions.map((item) => (item.id === region.id ? { ...item, ...patch } : item)));
  const length = region.end - region.start;
  return (
    <PanelSection title={L(`Vùng âm gốc #${index}`, `Original region #${index}`)}>
      <InfoRow label={L("Thời gian", "Time")}>
        {formatTime(region.start)} → {formatTime(region.end)}
      </InfoRow>
      <InfoRow label={L("Độ dài", "Length")}>{length.toFixed(2)}s</InfoRow>
      <div className="mt-3 grid gap-3">
        <RangeField
          label={L("Âm lượng", "Volume")}
          value={region.volume}
          min={0}
          max={400}
          step={5}
          format={(v) => `${Math.round(v)}%`}
          onCommit={(volume) => update({ volume })}
        />
        <RangeField
          label={L("Tăng dần", "Fade in")}
          value={region.fade_in}
          min={0}
          max={Math.max(0.1, Math.min(10, length - region.fade_out))}
          step={0.1}
          format={(v) => `${v.toFixed(1)}s`}
          onCommit={(fade_in) => update({ fade_in: Math.round(fade_in * 100) / 100 })}
        />
        <RangeField
          label={L("Giảm dần", "Fade out")}
          value={region.fade_out}
          min={0}
          max={Math.max(0.1, Math.min(10, length - region.fade_in))}
          step={0.1}
          format={(v) => `${v.toFixed(1)}s`}
          onCommit={(fade_out) => update({ fade_out: Math.round(fade_out * 100) / 100 })}
        />
      </div>
      <div className="mt-3 flex gap-1.5">
        <Button
          size="sm"
          leadingIcon={<Play size={13} />}
          onClick={() => {
            if (workspace.audio.original_enabled === false) editor.saveAudio({ original_enabled: true });
            playback.playRange(region.start, region.end);
          }}
        >
          {L("Nghe vùng", "Play")}
        </Button>
        <Button
          size="sm"
          variant="danger"
          leadingIcon={<Trash2 size={13} />}
          onClick={() => {
            editor.saveRegions(workspace.original_regions.filter((item) => item.id !== region.id));
            editor.selectRegion(null);
          }}
        >
          {L("Xóa vùng", "Delete")}
        </Button>
      </div>
      <p className="mt-3 text-[11.5px] leading-5 text-[var(--text-tertiary)]">
        {L("Kéo 2 mép vùng trên timeline để trim, kéo chấm tròn để chỉnh fade.", "Drag the edges on the timeline to trim, the dots to fade.")}
      </p>
    </PanelSection>
  );
}

function ProjectInspector({ workspace, L }: { workspace: EditorWorkspace; L: Translate }) {
  const { t } = useT();
  const voiced = workspace.segments.filter((seg) => seg.audio_url).length;
  return (
    <>
      <PanelSection title={t.ed_project}>
        <InfoRow label={L("Tên", "Title")}>{workspace.title || workspace.job_id}</InfoRow>
        <InfoRow label={L("Độ dài", "Duration")}>{formatTime(workspace.duration)}</InfoRow>
        <InfoRow label={L("Kích thước", "Size")}>
          {workspace.width}×{workspace.height}
        </InfoRow>
        <InfoRow label={L("Ngôn ngữ gốc", "Source language")}>{workspace.source_language}</InfoRow>
        <InfoRow label={t.ed_tab_cues}>{workspace.segments.length}</InfoRow>
        <InfoRow label={t.ed_voiced}>{voiced}</InfoRow>
        <InfoRow label={L("Cập nhật", "Updated")}>{new Date(workspace.updated_at).toLocaleString()}</InfoRow>
      </PanelSection>
      <div className="p-4">
        <EmptyState
          size="sm"
          icon={<MousePointerClick size={18} />}
          title={t.ed_nothing_selected}
          description={t.ed_nothing_selected_desc}
        />
      </div>
    </>
  );
}

