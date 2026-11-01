import clsx from "clsx";
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FolderOpen,
  Pause,
  Pencil,
  Play,
  Plus,
  Search,
  Trash2,
  Upload,
  Waves,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { DragEvent as ReactDragEvent, ReactNode } from "react";
import {
  deleteMusic,
  getMusicCredits,
  getMusicSettings,
  importJamendo,
  importOpenverse,
  outputUrl,
  patchMusic,
  saveMusicSettings,
  searchJamendo,
  searchOpenverse,
  uploadMusic,
  type MusicSettings,
  type OnlineSearch,
} from "../../../api";
import { Badge, Button, EmptyState, InlineNotice, SearchInput, Spinner, Switch, TextField } from "../../../components/ui";
import { useT } from "../../../i18n";
import type { MusicAsset, MusicCredits, OnlineTrack } from "../../../types";
import { PanelSection } from "../controls";
import { errorText } from "../utils";
import { refreshLibrary, removeAsset, upsertAssets } from "./library";
import { ASSET_MIME, formatDuration } from "./model";
import type { AudioTracksController } from "./useAudioTracks";

type Source = "library" | "openverse" | "jamendo";

/** Mood chips: Vietnamese label → English Openverse query. */
const MOODS: { key: "mu_mood_happy" | "mu_mood_cinematic" | "mu_mood_lofi" | "mu_mood_epic" | "mu_mood_calm" | "mu_mood_tech"; query: string }[] = [
  { key: "mu_mood_happy", query: "happy upbeat" },
  { key: "mu_mood_cinematic", query: "cinematic" },
  { key: "mu_mood_lofi", query: "lofi chill" },
  { key: "mu_mood_epic", query: "epic orchestral" },
  { key: "mu_mood_calm", query: "calm ambient piano" },
  { key: "mu_mood_tech", query: "technology electronic" },
];

/** One <audio> element for every preview in the panel: only one thing plays at a time. */
function usePreview(pausePreview: () => void) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  useEffect(() => {
    const element = new Audio();
    element.preload = "none";
    element.onended = () => setPlayingId(null);
    audio.current = element;
    return () => {
      element.pause();
      element.src = "";
    };
  }, []);
  const toggle = useCallback(
    (id: string, url: string | null) => {
      const element = audio.current;
      if (!element) return;
      if (playingId === id) {
        element.pause();
        setPlayingId(null);
        return;
      }
      if (!url) return;
      pausePreview();
      element.src = url;
      setFailedId(null);
      setPlayingId(id);
      element.play().catch(() => {
        setPlayingId(null);
        setFailedId(id);
      });
    },
    [playingId, pausePreview],
  );
  const stop = useCallback(() => {
    audio.current?.pause();
    setPlayingId(null);
  }, []);
  return { playingId, failedId, toggle, stop };
}

/** Left-panel "Music" tab: the shared library, Openverse / Jamendo search, imports and credits. */
export function MusicPanel({
  music,
  jobId,
  getPlayhead,
  pausePreview,
}: {
  music: AudioTracksController;
  jobId: string;
  getPlayhead: () => number;
  pausePreview: () => void;
}) {
  const { t } = useT();
  const [source, setSource] = useState<Source>("library");
  const preview = usePreview(pausePreview);
  useEffect(() => preview.stop, [source]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="flex min-h-full flex-col">
      <div className="sticky top-0 z-[2] border-b border-[var(--border-subtle)] bg-[var(--surface)] px-3 pb-2.5 pt-3">
        <div role="tablist" className="grid grid-cols-[1.45fr_1fr_1fr] gap-1 rounded-[var(--radius-control)] bg-[var(--surface-sunken)] p-1">
          {(
            [
              ["library", t.mu_src_library],
              ["openverse", "Openverse"],
              ["jamendo", "Jamendo"],
            ] as [Source, string][]
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={source === id}
              onClick={() => setSource(id)}
              className={clsx(
                "flex min-h-7 items-center justify-center gap-1 truncate rounded-[6px] px-1.5 text-[11.5px] font-semibold transition",
                source === id
                  ? "bg-[var(--surface)] text-[var(--primary-hover)] shadow-[var(--shadow-sm)]"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
              )}
            >
              <span className="truncate">{label}</span>
            </button>
          ))}
        </div>
      </div>
      {source === "library" ? (
        <LibraryTab music={music} jobId={jobId} getPlayhead={getPlayhead} preview={preview} />
      ) : source === "openverse" ? (
        <OnlineTab provider="openverse" music={music} getPlayhead={getPlayhead} preview={preview} />
      ) : (
        <JamendoTab music={music} getPlayhead={getPlayhead} preview={preview} />
      )}
    </div>
  );
}

type Preview = ReturnType<typeof usePreview>;

// ---- my library ----------------------------------------------------------------------

function LibraryTab({
  music,
  jobId,
  getPlayhead,
  preview,
}: {
  music: AudioTracksController;
  jobId: string;
  getPlayhead: () => number;
  preview: Preview;
}) {
  const { t } = useT();
  const { library } = music;
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [kind, setKind] = useState<"" | "music" | "sfx">("");
  const [uploading, setUploading] = useState(0);
  const [message, setMessage] = useState<{ tone: "danger" | "success"; text: string } | null>(null);
  const [dropping, setDropping] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const upload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(files.length);
    setMessage(null);
    try {
      const result = await uploadMusic(files, kind === "sfx" ? "sfx" : "music");
      upsertAssets(result.items);
      setMessage(
        result.errors.length
          ? { tone: "danger", text: result.errors.map((item) => `${item.filename}: ${item.error}`).join(" · ") }
          : { tone: "success", text: t.mu_imported_n.replace("{n}", String(result.items.length)) },
      );
    } catch (e) {
      setMessage({ tone: "danger", text: errorText(e) });
    } finally {
      setUploading(0);
    }
  };

  const onDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault();
    setDropping(false);
    void upload([...event.dataTransfer.files]);
  };

  const words = query.trim().toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").split(/\s+/).filter(Boolean);
  const fold = (text: string) => text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d");
  const visible = library.items.filter((asset) => {
    if (kind && asset.kind !== kind) return false;
    if (tag && !asset.tags.includes(tag)) return false;
    const hay = fold([asset.title, asset.artist, ...asset.tags].join(" "));
    return words.every((word) => hay.includes(word));
  });

  return (
    <div
      className={clsx("relative flex flex-1 flex-col", dropping && "outline-dashed outline-2 -outline-offset-4 outline-[var(--primary)]")}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setDropping(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDropping(false);
      }}
      onDrop={onDrop}
    >
      <div className="grid gap-2 border-b border-[var(--border-subtle)] p-3">
        <div className="flex gap-1.5">
          <SearchInput
            wrapperClassName="min-w-0 flex-1"
            placeholder={t.mu_search_library}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button size="sm" variant="primary" leadingIcon={<Upload size={14} />} loading={!!uploading} onClick={() => fileRef.current?.click()}>
            {t.mu_import}
          </Button>
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac"
            onChange={(event) => {
              void upload([...(event.target.files ?? [])]);
              event.target.value = "";
            }}
          />
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {(
            [
              ["", t.mu_kind_all],
              ["music", t.mu_kind_music],
              ["sfx", t.mu_kind_sfx],
            ] as ["" | "music" | "sfx", string][]
          ).map(([value, label]) => (
            <Chip key={value || "all"} active={kind === value} onClick={() => setKind(value)}>
              {label}
            </Chip>
          ))}
          {library.tags.length ? <span className="mx-0.5 h-4 w-px bg-[var(--border)]" /> : null}
          {library.tags.slice(0, 8).map((item) => (
            <Chip key={item} active={tag === item} onClick={() => setTag(tag === item ? "" : item)}>
              #{item}
            </Chip>
          ))}
        </div>
        {uploading ? (
          <p className="flex items-center gap-2 text-[12px] text-[var(--text-secondary)]">
            <Spinner size={13} /> {t.mu_importing.replace("{n}", String(uploading))}
          </p>
        ) : null}
        {message ? <InlineNotice tone={message.tone}>{message.text}</InlineNotice> : null}
        {library.error ? <InlineNotice tone="danger">{library.error}</InlineNotice> : null}
      </div>

      <div className="flex-1">
        {!library.loaded && library.loading ? (
          <div className="flex justify-center p-6">
            <Spinner />
          </div>
        ) : !library.items.length ? (
          <div className="p-4">
            <EmptyState
              size="sm"
              icon={<FolderOpen size={18} />}
              title={t.mu_empty_title}
              description={t.mu_empty_desc}
              action={
                <Button size="sm" leadingIcon={<Upload size={14} />} onClick={() => fileRef.current?.click()}>
                  {t.mu_import}
                </Button>
              }
            />
          </div>
        ) : !visible.length ? (
          <p className="p-4 text-[12.5px] text-[var(--text-tertiary)]">{t.mu_no_match}</p>
        ) : (
          <ul className="py-1">
            {visible.map((asset) => (
              <AssetRow
                key={asset.id}
                asset={asset}
                playing={preview.playingId === asset.id}
                onPlay={() => preview.toggle(asset.id, outputUrl(asset.file_url))}
                onAdd={() => music.addAsset(asset, getPlayhead())}
              />
            ))}
          </ul>
        )}
      </div>
      <p className="px-3 pb-2 text-[11px] leading-4 text-[var(--text-tertiary)]">{t.mu_drag_hint}</p>
      <CreditsSection jobId={jobId} usedCount={music.tracks.reduce((sum, track) => sum + track.clips.length, 0)} />
    </div>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={clsx(
        "rounded-full border px-2 py-0.5 text-[11.5px] font-medium transition-colors",
        active
          ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
          : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]",
      )}
    >
      {children}
    </button>
  );
}

function LicenseBadge({ label, commercial, code }: { label: string; commercial: boolean; code: string }) {
  const { t } = useT();
  return (
    <Badge size="sm" tone={code === "own" ? "neutral" : commercial ? "success" : "warning"}>
      <span title={commercial ? t.mu_license_ok : t.mu_license_nc}>{code === "own" ? t.mu_license_own : label}</span>
    </Badge>
  );
}

function AssetRow({ asset, playing, onPlay, onAdd }: { asset: MusicAsset; playing: boolean; onPlay: () => void; onAdd: () => void }) {
  const { t } = useT();
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!confirmDelete) return;
    const timer = window.setTimeout(() => setConfirmDelete(false), 3000);
    return () => window.clearTimeout(timer);
  }, [confirmDelete]);
  const remove = async () => {
    try {
      await deleteMusic(asset.id);
      removeAsset(asset.id);
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <li
      draggable={!asset.missing}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData(
          ASSET_MIME,
          JSON.stringify({ id: asset.id, title: asset.title, duration: asset.duration, kind: asset.kind }),
        );
        event.dataTransfer.setData("text/plain", asset.title);
      }}
      className="group border-b border-[var(--border-subtle)] px-3 py-2 last:border-b-0 hover:bg-[var(--surface-hover)]"
      data-testid="music-asset"
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label={playing ? t.mu_stop : t.mu_play}
          title={playing ? t.mu_stop : t.mu_play}
          onClick={onPlay}
          disabled={asset.missing}
          className={clsx(
            "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-colors disabled:opacity-40",
            playing
              ? "border-[var(--primary)] bg-[var(--primary)] text-white"
              : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--primary)] hover:text-[var(--primary)]",
          )}
        >
          {playing ? <Pause size={13} /> : <Play size={13} className="translate-x-px" />}
        </button>
        <div className="min-w-0 flex-1 cursor-grab active:cursor-grabbing">
          <div className="flex items-center gap-1.5">
            {asset.kind === "sfx" ? <Waves size={12} className="shrink-0 text-[var(--accent-pink)]" /> : null}
            <span className="truncate text-[12.5px] font-medium text-[var(--text-primary)]">{asset.title}</span>
          </div>
          <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
            <span className="font-mono">{formatDuration(asset.duration)}</span>
            {asset.artist ? <span className="truncate">· {asset.artist}</span> : null}
          </div>
        </div>
        <LicenseBadge label={asset.license_label} commercial={asset.commercial_ok} code={asset.license} />
      </div>
      {asset.missing ? <p className="mt-1 text-[11.5px] text-[var(--danger)]">{t.mu_missing_asset}</p> : null}
      {error ? <p className="mt-1 text-[11.5px] text-[var(--danger)]">{error}</p> : null}
      <div className="mt-1.5 flex items-center gap-1 pl-10">
        <Button size="xs" variant="outline" leadingIcon={<Plus size={12} />} onClick={onAdd} disabled={asset.missing}>
          {t.mu_add_at_playhead}
        </Button>
        <span className="flex-1" />
        <button
          type="button"
          title={t.mu_edit}
          aria-label={t.mu_edit}
          onClick={() => setEditing((value) => !value)}
          className="flex h-6 w-6 items-center justify-center rounded-[5px] text-[var(--text-tertiary)] hover:bg-[var(--surface-sunken)] hover:text-[var(--text-primary)]"
        >
          <Pencil size={12} />
        </button>
        <button
          type="button"
          title={confirmDelete ? t.mu_delete_confirm : t.mu_delete}
          aria-label={confirmDelete ? t.mu_delete_confirm : t.mu_delete}
          onClick={() => (confirmDelete ? void remove() : setConfirmDelete(true))}
          className={clsx(
            "flex h-6 items-center justify-center gap-1 rounded-[5px] px-1.5 text-[11px]",
            confirmDelete ? "bg-[var(--danger)] text-white" : "text-[var(--text-tertiary)] hover:bg-[var(--surface-sunken)] hover:text-[var(--danger)]",
          )}
        >
          <Trash2 size={12} />
          {confirmDelete ? t.mu_delete_confirm : null}
        </button>
      </div>
      {editing ? <AssetEditor asset={asset} onDone={() => setEditing(false)} /> : null}
    </li>
  );
}

function AssetEditor({ asset, onDone }: { asset: MusicAsset; onDone: () => void }) {
  const { t } = useT();
  const [title, setTitle] = useState(asset.title);
  const [artist, setArtist] = useState(asset.artist);
  const [tags, setTags] = useState(asset.tags.join(", "));
  const [kind, setKind] = useState(asset.kind);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setSaving(true);
    try {
      const updated = await patchMusic(asset.id, {
        title,
        artist,
        kind,
        tags: tags.split(",").map((item) => item.trim()).filter(Boolean),
      });
      upsertAssets([updated]);
      void refreshLibrary(); // tag list
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="mt-2 grid gap-1.5 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-muted)] p-2">
      <TextField aria-label={t.mu_title} placeholder={t.mu_title} value={title} onChange={(e) => setTitle(e.target.value)} className="!min-h-8 text-[12.5px]" />
      <TextField aria-label={t.mu_artist} placeholder={t.mu_artist} value={artist} onChange={(e) => setArtist(e.target.value)} className="!min-h-8 text-[12.5px]" />
      <TextField aria-label={t.mu_tags} placeholder={t.mu_tags_ph} value={tags} onChange={(e) => setTags(e.target.value)} className="!min-h-8 text-[12.5px]" />
      <div className="flex gap-1">
        <Chip active={kind === "music"} onClick={() => setKind("music")}>
          {t.mu_kind_music}
        </Chip>
        <Chip active={kind === "sfx"} onClick={() => setKind("sfx")}>
          {t.mu_kind_sfx}
        </Chip>
      </div>
      {error ? <p className="text-[11.5px] text-[var(--danger)]">{error}</p> : null}
      <div className="flex justify-end gap-1">
        <Button size="xs" variant="ghost" onClick={onDone}>
          {t.mu_cancel}
        </Button>
        <Button size="xs" variant="primary" loading={saving} onClick={() => void save()}>
          {t.mu_save}
        </Button>
      </div>
    </div>
  );
}

// ---- credits --------------------------------------------------------------------------------

function CreditsSection({ jobId, usedCount }: { jobId: string; usedCount: number }) {
  const { t, lang } = useT();
  const [credits, setCredits] = useState<MusicCredits | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let alive = true;
    getMusicCredits(jobId, lang === "vi" ? "vi" : "en")
      .then((data) => alive && setCredits(data))
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [jobId, lang, usedCount]);
  const copy = async () => {
    if (!credits?.text) return;
    try {
      await navigator.clipboard.writeText(credits.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError(t.mu_copy_failed);
    }
  };
  return (
    <PanelSection
      title={t.mu_credits}
      className="border-t border-[var(--border-subtle)] bg-[var(--surface-muted)]"
      action={
        <Button size="xs" variant="outline" leadingIcon={copied ? <Check size={12} /> : <Copy size={12} />} disabled={!credits?.text} onClick={() => void copy()}>
          {copied ? t.mu_copied : t.mu_copy}
        </Button>
      }
    >
      {error ? <p className="text-[11.5px] text-[var(--danger)]">{error}</p> : null}
      {credits?.text ? (
        <pre
          className="max-h-36 overflow-auto whitespace-pre-wrap rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface)] p-2 font-[var(--font-ui)] text-[11.5px] leading-[1.5] text-[var(--text-secondary)]"
          data-testid="music-credits"
        >
          {credits.text}
        </pre>
      ) : (
        <p className="text-[11.5px] leading-5 text-[var(--text-tertiary)]">{t.mu_credits_none}</p>
      )}
      {credits?.non_commercial.length ? <InlineNotice tone="warning">{t.mu_credits_nc}</InlineNotice> : null}
      {credits?.missing.length ? <p className="mt-1 text-[11.5px] text-[var(--danger)]">{t.mu_credits_missing.replace("{n}", String(credits.missing.length))}</p> : null}
    </PanelSection>
  );
}

// ---- online sources --------------------------------------------------------------------------

function JamendoTab({ music, getPlayhead, preview }: { music: AudioTracksController; getPlayhead: () => number; preview: Preview }) {
  const { t } = useT();
  const [settings, setSettings] = useState<MusicSettings | null>(null);
  const [clientId, setClientId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    getMusicSettings().then(setSettings).catch((e) => setError(errorText(e)));
  }, []);
  const save = async () => {
    try {
      setSettings(await saveMusicSettings({ jamendo_client_id: clientId }));
      setClientId("");
      setEditing(false);
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  };
  if (!settings) return <div className="flex justify-center p-6">{error ? <InlineNotice tone="danger">{error}</InlineNotice> : <Spinner />}</div>;
  if (!settings.jamendo_configured || editing) {
    return (
      <div className="grid gap-3 p-3">
        <InlineNotice tone="info" title={t.mu_jamendo_setup_title}>
          {t.mu_jamendo_setup_desc}{" "}
          <a className="font-medium text-[var(--text-link)] underline" href="https://devportal.jamendo.com/" target="_blank" rel="noreferrer">
            devportal.jamendo.com
          </a>
        </InlineNotice>
        <TextField placeholder="client_id" aria-label="Jamendo client_id" value={clientId} onChange={(e) => setClientId(e.target.value)} />
        {error ? <p className="text-[11.5px] text-[var(--danger)]">{error}</p> : null}
        <div className="flex gap-1.5">
          <Button size="sm" variant="primary" disabled={!clientId.trim()} onClick={() => void save()}>
            {t.mu_save}
          </Button>
          {editing ? (
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              {t.mu_cancel}
            </Button>
          ) : null}
        </div>
      </div>
    );
  }
  return (
    <>
      <OnlineTab provider="jamendo" music={music} getPlayhead={getPlayhead} preview={preview} />
      <p className="px-3 pb-3 text-[11px] text-[var(--text-tertiary)]">
        client_id {settings.jamendo_client_id} ·{" "}
        <button type="button" className="text-[var(--text-link)] underline" onClick={() => setEditing(true)}>
          {t.mu_change}
        </button>
      </p>
    </>
  );
}

function OnlineTab({
  provider,
  music,
  getPlayhead,
  preview,
}: {
  provider: "openverse" | "jamendo";
  music: AudioTracksController;
  getPlayhead: () => number;
  preview: Preview;
}) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<"music" | "sound_effect">("music");
  const [commercialOnly, setCommercialOnly] = useState(true);
  const [result, setResult] = useState<OnlineSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState<Record<string, "busy" | MusicAsset>>({});
  const lastQuery = useRef("");

  const search = async (q: string, page = 1) => {
    const text = q.trim();
    if (!text) return;
    lastQuery.current = text;
    setLoading(true);
    setError(null);
    try {
      const data =
        provider === "openverse"
          ? await searchOpenverse({ q: text, category, commercial_only: commercialOnly, page })
          : await searchJamendo({ q: text, commercial_only: commercialOnly, page });
      setResult((current) => (page > 1 && current ? { ...data, items: [...current.items, ...data.items] } : data));
    } catch (e) {
      setError(errorText(e));
      if (page === 1) setResult(null);
    } finally {
      setLoading(false);
    }
  };

  // Re-run the last search when a filter changes.
  useEffect(() => {
    if (lastQuery.current) void search(lastQuery.current);
  }, [category, commercialOnly]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async (item: OnlineTrack) => {
    setImporting((current) => ({ ...current, [item.id]: "busy" }));
    try {
      const asset = provider === "openverse" ? await importOpenverse(item.id) : await importJamendo(item.id);
      upsertAssets([asset]);
      setImporting((current) => ({ ...current, [item.id]: asset }));
    } catch (e) {
      setError(errorText(e));
      setImporting((current) => {
        const next = { ...current };
        delete next[item.id];
        return next;
      });
    }
  };

  const more = result && result.page_count !== null ? result.page < (result.page_count ?? 1) : (result?.items.length ?? 0) % 20 === 0 && !!result?.items.length;
  return (
    <div className="flex flex-1 flex-col">
      <div className="grid gap-2 border-b border-[var(--border-subtle)] p-3">
        <form
          className="flex gap-1.5"
          onSubmit={(event) => {
            event.preventDefault();
            void search(query);
          }}
        >
          <SearchInput
            wrapperClassName="min-w-0 flex-1"
            placeholder={t.mu_search_online}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Button size="sm" type="submit" variant="primary" loading={loading}>
            {t.mu_search}
          </Button>
        </form>
        <div className="flex flex-wrap gap-1">
          {MOODS.map((mood) => (
            <Chip
              key={mood.key}
              active={lastQuery.current === mood.query}
              onClick={() => {
                setQuery(mood.query);
                void search(mood.query);
              }}
            >
              {t[mood.key]}
            </Chip>
          ))}
        </div>
        {provider === "openverse" ? (
          <div className="flex gap-1">
            <Chip active={category === "music"} onClick={() => setCategory("music")}>
              {t.mu_kind_music}
            </Chip>
            <Chip active={category === "sound_effect"} onClick={() => setCategory("sound_effect")}>
              {t.mu_kind_sfx}
            </Chip>
          </div>
        ) : null}
        <Switch checked={commercialOnly} onChange={setCommercialOnly} label={t.mu_commercial_only} description={t.mu_commercial_only_desc} />
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      </div>
      <div className="flex-1">
        {!result && !loading ? (
          <div className="p-4">
            <EmptyState
              size="sm"
              icon={<Search size={18} />}
              title={provider === "openverse" ? t.mu_openverse_title : t.mu_jamendo_title}
              description={provider === "openverse" ? t.mu_openverse_desc : t.mu_jamendo_desc}
            />
          </div>
        ) : result && !result.items.length ? (
          <p className="p-4 text-[12.5px] text-[var(--text-tertiary)]">{t.mu_no_results}</p>
        ) : (
          <ul className="py-1" data-testid="online-results">
            {result?.items.map((item) => {
              const state = importing[item.id];
              const asset = state && state !== "busy" ? state : null;
              const known = asset ?? (item.imported ? music.library.items.find((entry) => entry.source_id === item.id) : undefined);
              return (
                <li key={item.id} className="border-b border-[var(--border-subtle)] px-3 py-2 last:border-b-0">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      aria-label={preview.playingId === item.id ? t.mu_stop : t.mu_play}
                      title={preview.failedId === item.id ? t.mu_preview_failed : preview.playingId === item.id ? t.mu_stop : t.mu_play}
                      onClick={() => preview.toggle(item.id, item.preview_url)}
                      className={clsx(
                        "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-colors",
                        preview.playingId === item.id
                          ? "border-[var(--primary)] bg-[var(--primary)] text-white"
                          : preview.failedId === item.id
                            ? "border-[var(--danger-border)] text-[var(--danger)]"
                            : "border-[var(--border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--primary)] hover:text-[var(--primary)]",
                      )}
                    >
                      {preview.playingId === item.id ? <Pause size={13} /> : <Play size={13} className="translate-x-px" />}
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[12.5px] font-medium text-[var(--text-primary)]" title={item.title}>
                        {item.title || "—"}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
                        <span className="font-mono">{formatDuration(item.duration)}</span>
                        <span className="truncate">· {item.artist || "—"}</span>
                      </div>
                    </div>
                    <LicenseBadge label={item.license_label} commercial={item.commercial_ok} code={item.license} />
                  </div>
                  <div className="mt-1.5 flex items-center gap-1 pl-10">
                    {known ? (
                      <>
                        <Badge size="sm" tone="success" icon={<Check size={11} />}>
                          {t.mu_in_library}
                        </Badge>
                        <Button size="xs" variant="outline" leadingIcon={<Plus size={12} />} onClick={() => music.addAsset(known, getPlayhead())}>
                          {t.mu_add_at_playhead}
                        </Button>
                      </>
                    ) : (
                      <Button
                        size="xs"
                        variant="outline"
                        leadingIcon={<Download size={12} />}
                        loading={state === "busy"}
                        onClick={() => void add(item)}
                      >
                        {t.mu_add_to_library}
                      </Button>
                    )}
                    <span className="flex-1" />
                    <span className="truncate text-[10.5px] text-[var(--text-tertiary)]">{item.source}</span>
                    {item.landing_url ? (
                      <a
                        href={item.landing_url}
                        target="_blank"
                        rel="noreferrer"
                        title={t.mu_open_source}
                        aria-label={t.mu_open_source}
                        className="flex h-6 w-6 items-center justify-center rounded-[5px] text-[var(--text-tertiary)] hover:bg-[var(--surface-sunken)] hover:text-[var(--text-primary)]"
                      >
                        <ExternalLink size={12} />
                      </a>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {result && more ? (
          <div className="p-3">
            <Button size="sm" variant="ghost" block loading={loading} onClick={() => void search(lastQuery.current, (result.page ?? 1) + 1)}>
              {t.mu_load_more}
            </Button>
          </div>
        ) : null}
      </div>
      <p className="px-3 py-2 text-[11px] leading-4 text-[var(--text-tertiary)]">
        {provider === "openverse" ? t.mu_openverse_note : t.mu_jamendo_note}
      </p>
    </div>
  );
}
