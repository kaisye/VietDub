import { ExternalLink, Play, Scissors, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Select, Switch } from "../../../components/ui";
import { useT } from "../../../i18n";
import type { EditorAudioClip, EditorAudioTrack, EditorWorkspace } from "../../../types";
import { InfoRow, PanelSection, RangeField } from "../controls";
import type { EditorController } from "../useEditor";
import type { Playback } from "../usePlayback";
import { formatTime } from "../utils";
import { clipEnd, formatDuration, MIN_CLIP, round3 } from "./model";
import type { AudioTracksController } from "./useAudioTracks";

/** Seconds field that commits on blur / Enter (one undo step per commit). */
function SecondsField({ label, value, onCommit, min = 0 }: { label: string; value: number; onCommit: (v: number) => void; min?: number }) {
  const [text, setText] = useState(value.toFixed(2));
  useEffect(() => setText(value.toFixed(2)), [value]);
  const commit = () => {
    const parsed = Number(text.replace(",", "."));
    if (!Number.isFinite(parsed) || parsed < min) {
      setText(value.toFixed(2));
      return;
    }
    if (Math.abs(parsed - value) > 0.0005) onCommit(round3(parsed));
  };
  return (
    <label className="grid min-w-0 gap-1">
      <span className="text-[11.5px] font-medium text-[var(--text-secondary)]">{label}</span>
      <input
        value={text}
        inputMode="decimal"
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setText(value.toFixed(2));
        }}
        className="h-8 w-full min-w-0 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] px-2 text-center font-mono text-[12.5px] outline-none focus:border-[var(--primary)] focus:shadow-[var(--shadow-focus)]"
      />
    </label>
  );
}

export function ClipInspector({
  editor,
  playback,
  music,
  getPlayhead,
}: {
  editor: EditorController;
  playback: Playback;
  music: AudioTracksController;
  getPlayhead: () => number;
}) {
  const { t } = useT();
  const workspace = editor.workspace as EditorWorkspace;
  const tracks = music.tracks;
  const found = tracks
    .flatMap((track) => track.clips.map((clip) => ({ track, clip })))
    .find((item) => item.clip.id === editor.clipSelection[0]);
  if (!found) return null;
  const { track, clip } = found;
  const asset = music.assets.get(clip.asset_id);
  const locked = track.locked;

  const update = (patch: Partial<EditorAudioClip>, kind = `clip:${clip.id}`) => {
    const next = { ...clip, ...patch, ducking: { ...clip.ducking, ...(patch.ducking ?? {}) } };
    const assetDuration = (patch.asset_id ? music.assets.get(patch.asset_id)?.duration : asset?.duration) ?? Infinity;
    next.start = Math.max(0, Math.min(next.start, workspace.duration - MIN_CLIP));
    next.in = Math.max(0, next.loop ? next.in : Math.min(next.in, Math.max(0, assetDuration - MIN_CLIP)));
    const room = workspace.duration - next.start;
    next.duration = Math.max(MIN_CLIP, Math.min(next.duration, room, next.loop ? Infinity : assetDuration - next.in));
    next.fade_in = Math.min(next.fade_in, next.duration);
    next.fade_out = Math.min(next.fade_out, next.duration - next.fade_in);
    editor.saveAudioTracks(
      tracks.map((item: EditorAudioTrack) =>
        item.id === track.id
          ? { ...item, clips: item.clips.map((c) => (c.id === clip.id ? next : c)).sort((a, b) => a.start - b.start) }
          : item,
      ),
      kind,
    );
  };

  const playhead = getPlayhead();
  const canSplit = !locked && playhead > clip.start + MIN_CLIP && playhead < clipEnd(clip) - MIN_CLIP;
  const sameKind = music.library.items.filter((item) => !item.missing);

  return (
    <>
      <PanelSection title={t.mu_clip_source}>
        <div className="mb-2 flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] font-semibold text-[var(--text-primary)]" title={asset?.title ?? clip.title}>
              {asset?.title ?? clip.title ?? clip.asset_id}
            </div>
            <div className="truncate text-[11.5px] text-[var(--text-tertiary)]">
              {asset?.artist || "—"} · {formatDuration(asset?.duration)}
            </div>
          </div>
          {asset ? (
            <Badge size="sm" tone={asset.license === "own" ? "neutral" : asset.commercial_ok ? "success" : "warning"}>
              {asset.license === "own" ? t.mu_license_own : asset.license_label}
            </Badge>
          ) : (
            <Badge size="sm" tone="danger">
              {t.mu_missing_short}
            </Badge>
          )}
        </div>
        {!asset || asset.missing ? <p className="mb-2 text-[11.5px] text-[var(--danger)]">{t.mu_missing_asset}</p> : null}
        {asset?.attribution_required ? (
          <div className="mb-2 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-muted)] px-2.5 py-2 text-[11.5px] leading-[1.5] text-[var(--text-secondary)]">
            <div className="mb-0.5 font-semibold text-[var(--text-primary)]">{t.mu_attribution_required}</div>
            {asset.attribution}
          </div>
        ) : null}
        {asset && !asset.commercial_ok && asset.license !== "own" ? (
          <p className="mb-2 text-[11.5px] text-[var(--warning)]">{t.mu_license_nc}</p>
        ) : null}
        <div className="flex flex-wrap gap-2 text-[11.5px]">
          {asset?.license_url ? (
            <a className="inline-flex items-center gap-1 text-[var(--text-link)] hover:underline" href={asset.license_url} target="_blank" rel="noreferrer">
              <ExternalLink size={11} /> {t.mu_license}
            </a>
          ) : null}
          {asset?.source_url ? (
            <a className="inline-flex items-center gap-1 text-[var(--text-link)] hover:underline" href={asset.source_url} target="_blank" rel="noreferrer">
              <ExternalLink size={11} /> {t.mu_open_source}
            </a>
          ) : null}
        </div>
        <label className="mt-3 grid gap-1 text-[11.5px] font-medium text-[var(--text-secondary)]">
          {t.mu_replace_asset}
          <Select
            value={clip.asset_id}
            disabled={locked}
            onChange={(e) => {
              const replacement = music.assets.get(e.target.value);
              if (replacement) update({ asset_id: replacement.id, title: replacement.title }, "replace-asset");
            }}
            className="!min-h-8 text-[12.5px]"
          >
            {!asset ? <option value={clip.asset_id}>{clip.title ?? clip.asset_id}</option> : null}
            {sameKind.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} ({formatDuration(item.duration)})
              </option>
            ))}
          </Select>
        </label>
      </PanelSection>

      <PanelSection title={t.ed_timing}>
        <div className="grid grid-cols-3 gap-2">
          <SecondsField label={t.mu_start} value={clip.start} onCommit={(start) => update({ start })} />
          <SecondsField label={t.mu_in} value={clip.in} onCommit={(value) => update({ in: value })} />
          <SecondsField label={t.mu_length} value={clip.duration} min={MIN_CLIP} onCommit={(duration) => update({ duration })} />
        </div>
        <InfoRow label={t.ed_range}>
          {formatTime(clip.start)} → {formatTime(clipEnd(clip))}
        </InfoRow>
      </PanelSection>

      <PanelSection title={t.mu_sound}>
        <div className="grid gap-3">
          <RangeField
            label={t.mu_volume}
            value={clip.volume}
            min={0}
            max={200}
            format={(v) => `${Math.round(v)}%`}
            disabled={locked}
            onCommit={(volume) => update({ volume })}
          />
          <RangeField
            label={t.mu_fade_in}
            value={clip.fade_in}
            min={0}
            max={Math.max(0.1, Math.min(15, clip.duration - clip.fade_out))}
            step={0.1}
            format={(v) => `${v.toFixed(1)}s`}
            disabled={locked}
            onCommit={(fade_in) => update({ fade_in: round3(fade_in) })}
          />
          <RangeField
            label={t.mu_fade_out}
            value={clip.fade_out}
            min={0}
            max={Math.max(0.1, Math.min(15, clip.duration - clip.fade_in))}
            step={0.1}
            format={(v) => `${v.toFixed(1)}s`}
            disabled={locked}
            onCommit={(fade_out) => update({ fade_out: round3(fade_out) })}
          />
          <Switch checked={clip.loop} disabled={locked} onChange={(loop) => update({ loop })} label={t.mu_loop} description={t.mu_loop_desc} />
        </div>
      </PanelSection>

      <PanelSection title={t.mu_ducking}>
        <div className="grid gap-3">
          <Switch
            checked={clip.ducking.enabled}
            disabled={locked}
            onChange={(enabled) => update({ ducking: { ...clip.ducking, enabled } })}
            label={t.mu_ducking_on}
            description={t.mu_ducking_desc}
          />
          <RangeField
            label={t.mu_ducking_amount}
            value={clip.ducking.amount_db}
            min={-30}
            max={0}
            step={1}
            format={(v) => `${Math.round(v)} dB`}
            disabled={locked || !clip.ducking.enabled}
            onCommit={(amount_db) => update({ ducking: { ...clip.ducking, amount_db } })}
          />
          <div className="grid grid-cols-2 gap-3">
            <RangeField
              label={t.mu_attack}
              value={clip.ducking.attack}
              min={0.05}
              max={2}
              step={0.05}
              format={(v) => `${v.toFixed(2)}s`}
              disabled={locked || !clip.ducking.enabled}
              onCommit={(attack) => update({ ducking: { ...clip.ducking, attack: round3(attack) } })}
            />
            <RangeField
              label={t.mu_release}
              value={clip.ducking.release}
              min={0.05}
              max={3}
              step={0.05}
              format={(v) => `${v.toFixed(2)}s`}
              disabled={locked || !clip.ducking.enabled}
              onCommit={(release) => update({ ducking: { ...clip.ducking, release: round3(release) } })}
            />
          </div>
        </div>
      </PanelSection>

      <PanelSection title={t.ed_actions}>
        <div className="grid grid-cols-2 gap-1.5">
          <Button size="sm" leadingIcon={<Play size={13} />} onClick={() => playback.playRange(clip.start, clipEnd(clip))}>
            {t.mu_play_clip}
          </Button>
          <Button size="sm" leadingIcon={<Scissors size={13} />} disabled={!canSplit} onClick={() => editor.splitClipsAt(getPlayhead())}>
            {t.ed_split}
          </Button>
          <Button size="sm" variant="danger" className="col-span-2" leadingIcon={<Trash2 size={13} />} disabled={locked} onClick={editor.deleteClips}>
            {t.mu_delete_clip}
          </Button>
        </div>
        {locked ? <p className="mt-2 text-[11.5px] text-[var(--text-tertiary)]">{t.mu_track_locked}</p> : null}
      </PanelSection>
    </>
  );
}

export function MultiClipInspector({ editor, music }: { editor: EditorController; music: AudioTracksController }) {
  const { t } = useT();
  const chosen = music.tracks.flatMap((track) => track.clips.filter((clip) => editor.clipSelection.includes(clip.id)));
  const from = Math.min(...chosen.map((clip) => clip.start));
  const to = Math.max(...chosen.map(clipEnd));
  return (
    <PanelSection title={t.ed_selection}>
      <InfoRow label={t.ed_range}>{chosen.length ? `${formatTime(from)} → ${formatTime(to)}` : "—"}</InfoRow>
      <div className="mt-3">
        <Button size="sm" variant="danger" block leadingIcon={<Trash2 size={13} />} onClick={editor.deleteClips}>
          {t.ed_delete_n.replace("{n}", String(chosen.length))}
        </Button>
      </div>
    </PanelSection>
  );
}
