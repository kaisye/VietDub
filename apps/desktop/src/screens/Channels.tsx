import clsx from "clsx";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  CheckCircle2,
  Clapperboard,
  Download,
  ExternalLink,
  FileSpreadsheet,
  Film,
  KeyRound,
  Link2,
  LineChart as LineChartIcon,
  Plug,
  Plus,
  RefreshCw,
  Settings2,
  Trash2,
  Tv,
  Unlink,
  Unplug,
  Upload,
  UserPlus,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { listJobs } from "../api";
import { CopyButton } from "../components/assistant/CopyButton";
import { LineChart } from "../components/channels/charts";
import { JobStatusBadge, JobThumb, jobTitle } from "../components/channels/jobs";
import {
  ChannelStatusBadge,
  PLATFORM_CHART_COLOR,
  PLATFORM_LABEL,
  PlatformIcon,
  fmtCompact,
  fmtDateTime,
  fmtDuration,
  fmtNumber,
  fmtRelative,
  fmtShortDate,
  takeChannelsIntent,
} from "../components/channels/platform";
import { Page, PageHeader } from "../components/layout/PageHeader";
import type { View } from "../components/layout/nav";
import {
  Avatar,
  Badge,
  Button,
  Card,
  Drawer,
  EmptyState,
  Field,
  IconButton,
  InlineNotice,
  Modal,
  ProgressBar,
  SearchInput,
  Skeleton,
  Spinner,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  TableEmpty,
  Tabs,
  TextField,
} from "../components/ui";
import { tt, useT } from "../i18n";
import type { T } from "../i18n";
import {
  PLATFORMS,
  addSnapshot,
  createManualChannel,
  deleteChannel,
  disconnectChannel,
  getChannelSettings,
  getOAuthStatus,
  getSnapshots,
  getSyncStatus,
  importChannelCsv,
  linkVideoToJob,
  listChannelVideos,
  listChannels,
  parseUtc,
  startOAuth,
  syncChannel,
  syncChannels,
  updateChannelSettings,
  updatePlatformCredentials,
} from "../lib/channels-api";
import type {
  Channel,
  ChannelSettings,
  ChannelVideo,
  ImportResult,
  OAuthStatus,
  Platform,
  PlatformSettings,
  Snapshot,
  SyncStatus,
  VideoSort,
} from "../lib/channels-api";
import { openUrl } from "../lib/open-url";
import type { Job } from "../types";

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

export default function ChannelsScreen(_props: { onNavigate?: (view: View) => void }) {
  const { t, lang } = useT();
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [settings, setSettings] = useState<ChannelSettings | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "warning" | "danger"; text: string } | null>(null);
  const [filter, setFilter] = useState<Platform | "all">("all");
  const [connect, setConnect] = useState<{ open: boolean; platform?: Platform; reconnect?: boolean }>({ open: false });
  const [manualOpen, setManualOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "delete" | "disconnect"; channel: Channel } | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [syncTotal, setSyncTotal] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const [list, s] = await Promise.all([listChannels(), getChannelSettings()]);
      if (!mounted.current) return;
      setChannels(list);
      setSettings(s);
      setError("");
    } catch (err) {
      if (mounted.current) setError(errText(err));
    }
  }, []);

  useEffect(() => {
    void load();
    const intent = takeChannelsIntent();
    if (intent === "connect") setConnect({ open: true });
    else if (intent === "manual") setManualOpen(true);
    else if (intent === "settings") setSettingsOpen(true);
    else if (intent?.startsWith("detail:")) setDetailId(intent.slice(7));
  }, [load]);

  // --- sync -------------------------------------------------------------------
  const pollSync = useCallback(async () => {
    try {
      for (let i = 0; i < 300 && mounted.current; i += 1) {
        const status = await getSyncStatus();
        if (!mounted.current) return;
        setSync(status);
        if (!status.running) break;
        await new Promise((r) => setTimeout(r, 2000));
      }
    } catch (err) {
      if (mounted.current) setError(errText(err));
    }
    if (!mounted.current) return;
    await load();
    setSync((s) => (s ? { ...s, running: false } : s));
  }, [load]);

  // Pick up a sync that is already running (e.g. the dashboard's auto-sync).
  useEffect(() => {
    getSyncStatus()
      .then((s) => {
        if (s.running && mounted.current) {
          setSyncTotal(s.queued + 1 + s.results.length);
          void pollSync();
        }
      })
      .catch(() => undefined);
  }, [pollSync]);

  const runSync = async (channelIds?: string[]) => {
    try {
      const res = channelIds?.length === 1 ? await syncChannel(channelIds[0]) : await syncChannels(channelIds ? { channel_ids: channelIds } : {});
      setSyncTotal(res.queued_channel_ids.length);
      setSync({ running: true, current_channel_id: null, queued: res.queued_channel_ids.length, started_at: null, finished_at: null, results: [] });
      void pollSync();
    } catch (err) {
      setNotice({ tone: "danger", text: errText(err) });
    }
  };

  const syncing = !!sync?.running;
  const syncDone = sync ? sync.results.length : 0;
  const syncFailures = sync && !sync.running ? sync.results.filter((r) => !r.ok) : [];

  // --- actions ----------------------------------------------------------------
  const doConfirm = async () => {
    if (!confirm) return;
    const { kind, channel } = confirm;
    try {
      const res = kind === "delete" ? await deleteChannel(channel.id) : await disconnectChannel(channel.id);
      setNotice({
        tone: res.revoke_error ? "warning" : "success",
        text: res.revoke_error
          ? tt(t.chn_revoke_failed, { e: res.revoke_error })
          : tt(kind === "delete" ? t.chn_deleted : t.chn_disconnected, { c: channel.name }),
      });
      if (kind === "delete" && detailId === channel.id) setDetailId(null);
      setConfirm(null);
      void load();
    } catch (err) {
      setNotice({ tone: "danger", text: errText(err) });
      setConfirm(null);
    }
  };

  const visible = useMemo(
    () => (channels ?? []).filter((c) => filter === "all" || c.platform === filter),
    [channels, filter],
  );
  const counts = useMemo(() => {
    const out: Record<string, number> = { all: channels?.length ?? 0 };
    for (const p of PLATFORMS) out[p] = (channels ?? []).filter((c) => c.platform === p).length;
    return out;
  }, [channels]);
  const hasOAuth = (channels ?? []).some((c) => c.source === "oauth" && c.status !== "disconnected");
  const detail = channels?.find((c) => c.id === detailId) ?? null;

  return (
    <Page>
      <PageHeader
        icon={<Tv size={20} />}
        title={t.nav_channels}
        description={t.channels_sub}
        actions={
          <>
            <Button variant="ghost" leadingIcon={<Settings2 size={16} />} onClick={() => setSettingsOpen(true)}>
              {t.chn_platform_settings}
            </Button>
            {hasOAuth ? (
              <Button leadingIcon={<RefreshCw size={15} className={clsx(syncing && "animate-spin")} />} disabled={syncing} onClick={() => runSync()}>
                {syncing ? t.chn_syncing : t.chn_sync_all}
              </Button>
            ) : null}
            <Button leadingIcon={<UserPlus size={16} />} onClick={() => setManualOpen(true)}>
              {t.chn_add_manual}
            </Button>
            <Button variant="primary" leadingIcon={<Link2 size={16} />} onClick={() => setConnect({ open: true })}>
              {t.chn_connect}
            </Button>
          </>
        }
      >
        {channels && channels.length ? (
          <Tabs
            value={filter}
            onChange={(v) => setFilter(v as Platform | "all")}
            items={[
              { id: "all", label: t.dsh_all_platforms, count: counts.all },
              ...PLATFORMS.map((p) => ({
                id: p,
                label: PLATFORM_LABEL[p],
                icon: <PlatformIcon platform={p} size={14} />,
                count: counts[p],
              })),
            ]}
          />
        ) : null}
      </PageHeader>

      <div className="grid gap-4">
        {error ? (
          <InlineNotice tone="danger" title={t.dsh_load_error} action={<Button size="sm" onClick={() => void load()}>{t.dsh_retry}</Button>}>
            {error}
          </InlineNotice>
        ) : null}
        {notice ? (
          <InlineNotice tone={notice.tone} action={<Button size="xs" variant="ghost" onClick={() => setNotice(null)}>{t.chn_dismiss}</Button>}>
            {notice.text}
          </InlineNotice>
        ) : null}
        {sync && (syncing || syncFailures.length > 0) ? (
          <Card padding="sm">
            <div className="flex items-center gap-3">
              {syncing ? <Spinner /> : <XCircle size={16} className="text-[var(--danger)]" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between text-[13px]">
                  <span className="font-semibold text-[var(--text-primary)]">
                    {syncing
                      ? tt(t.chn_sync_progress, {
                          n: syncDone,
                          total: Math.max(syncTotal, syncDone + sync.queued + (sync.current_channel_id ? 1 : 0)),
                        })
                      : tt(t.chn_sync_failed_n, { n: syncFailures.length })}
                  </span>
                  {syncing && sync.current_channel_id ? (
                    <span className="truncate text-xs text-[var(--text-secondary)]">
                      {channels?.find((c) => c.id === sync.current_channel_id)?.name}
                    </span>
                  ) : null}
                  {!syncing ? (
                    <Button size="xs" variant="ghost" onClick={() => setSync(null)}>
                      {t.chn_dismiss}
                    </Button>
                  ) : null}
                </div>
                {syncing ? (
                  <ProgressBar
                    className="mt-2"
                    size="sm"
                    value={syncDone}
                    max={Math.max(1, syncTotal, syncDone + sync.queued + (sync.current_channel_id ? 1 : 0))}
                  />
                ) : (
                  <ul className="mt-1 grid gap-0.5 text-xs text-[var(--text-secondary)]">
                    {syncFailures.map((r) => (
                      <li key={r.channel_id} className="truncate">
                        <strong className="font-medium text-[var(--text-primary)]">
                          {channels?.find((c) => c.id === r.channel_id)?.name ?? r.channel_id.slice(0, 8)}
                        </strong>
                        : {r.error}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </Card>
        ) : null}

        {channels === null && !error ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-56" rounded="lg" />
            ))}
          </div>
        ) : channels && channels.length === 0 ? (
          <Card padding="lg">
            <EmptyState
              size="lg"
              icon={<Tv size={26} />}
              title={t.chn_empty_title}
              description={t.chn_empty_desc}
              action={
                <Button variant="primary" leadingIcon={<Link2 size={16} />} onClick={() => setConnect({ open: true })}>
                  {t.chn_connect}
                </Button>
              }
              secondaryAction={
                <Button leadingIcon={<UserPlus size={16} />} onClick={() => setManualOpen(true)}>
                  {t.chn_add_manual}
                </Button>
              }
            >
              <div className="mt-1 flex gap-2">
                {PLATFORMS.map((p) => (
                  <Badge key={p} icon={<PlatformIcon platform={p} size={12} />}>
                    {p === "facebook" ? t.dsh_platform_fb : PLATFORM_LABEL[p]}
                  </Badge>
                ))}
              </div>
            </EmptyState>
          </Card>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
            {visible.map((c) => (
              <ChannelCard
                key={c.id}
                t={t}
                lang={lang}
                channel={c}
                syncingNow={syncing && sync?.current_channel_id === c.id}
                syncBusy={syncing}
                onOpen={() => setDetailId(c.id)}
                onSync={() => runSync([c.id])}
                onReconnect={() => setConnect({ open: true, platform: c.platform, reconnect: true })}
                onDisconnect={() => setConfirm({ kind: "disconnect", channel: c })}
                onDelete={() => setConfirm({ kind: "delete", channel: c })}
              />
            ))}
            <button
              type="button"
              onClick={() => setConnect({ open: true })}
              className="flex min-h-56 flex-col items-center justify-center gap-2 rounded-[var(--radius-panel)] border border-dashed border-[var(--border-strong)] bg-transparent text-[13px] font-medium text-[var(--text-secondary)] transition hover:border-[var(--primary)] hover:bg-[var(--primary-soft)] hover:text-[var(--primary)]"
            >
              <Plus size={20} />
              {t.chn_add_another}
            </button>
          </div>
        )}
      </div>

      <ConnectModal
        t={t}
        open={connect.open}
        initialPlatform={connect.platform}
        reconnect={connect.reconnect}
        settings={settings}
        onClose={() => setConnect({ open: false })}
        onSettingsChanged={setSettings}
        onConnected={(list) => {
          void load();
          setNotice({ tone: "success", text: tt(t.chn_connected_n, { n: list.length }) });
        }}
        onManual={() => {
          setConnect({ open: false });
          setManualOpen(true);
        }}
      />
      <ManualModal
        t={t}
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        onCreated={(c) => {
          setManualOpen(false);
          void load();
          setDetailId(c.id);
        }}
      />
      <SettingsDrawer t={t} open={settingsOpen} settings={settings} onClose={() => setSettingsOpen(false)} onChanged={setSettings} />
      <ChannelDrawer
        t={t}
        lang={lang}
        channel={detail}
        onClose={() => setDetailId(null)}
        onChanged={() => void load()}
        onSync={() => detail && runSync([detail.id])}
        syncing={syncing}
      />
      <Modal
        open={!!confirm}
        onClose={() => setConfirm(null)}
        size="sm"
        title={confirm ? tt(confirm.kind === "delete" ? t.chn_confirm_delete_title : t.chn_confirm_disconnect_title, { c: confirm.channel.name }) : ""}
        description={confirm ? (confirm.kind === "delete" ? t.chn_confirm_delete_desc : t.chn_confirm_disconnect_desc) : ""}
        footer={
          <>
            <Button onClick={() => setConfirm(null)}>{t.chn_cancel}</Button>
            <Button variant="danger" onClick={doConfirm} leadingIcon={confirm?.kind === "delete" ? <Trash2 size={15} /> : <Unplug size={15} />}>
              {confirm?.kind === "delete" ? t.chn_delete : t.chn_disconnect}
            </Button>
          </>
        }
      />
    </Page>
  );
}

// --- Channel card ------------------------------------------------------------------

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11.5px] text-[var(--text-tertiary)]">{label}</div>
      <div className="truncate text-[15px] font-semibold tabular-nums text-[var(--text-primary)]">{value}</div>
    </div>
  );
}

function ChannelCard({
  t,
  lang,
  channel: c,
  syncingNow,
  syncBusy,
  onOpen,
  onSync,
  onReconnect,
  onDisconnect,
  onDelete,
}: {
  t: T;
  lang: string;
  channel: Channel;
  syncingNow: boolean;
  syncBusy: boolean;
  onOpen: () => void;
  onSync: () => void;
  onReconnect: () => void;
  onDisconnect: () => void;
  onDelete: () => void;
}) {
  const isOAuth = c.source === "oauth";
  const needsReconnect = isOAuth && (c.status === "needs_reauth" || c.status === "disconnected");
  return (
    <Card padding="none" className="overflow-hidden">
      <button type="button" onClick={onOpen} className="block w-full p-5 pb-4 text-left transition hover:bg-[var(--surface-hover)]">
        <div className="flex items-start gap-3">
          <Avatar src={c.avatar_url} name={c.name} size="lg" badge={<PlatformIcon platform={c.platform} size={16} />} />
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <h3 className="truncate text-[15px] font-semibold text-[var(--text-primary)]">{c.name}</h3>
              <ChannelStatusBadge t={t} status={c.status} title={c.last_error ?? undefined} />
            </div>
            <div className="mt-0.5 truncate text-xs text-[var(--text-secondary)]">
              {c.handle || PLATFORM_LABEL[c.platform]} · {isOAuth ? t.chn_source_oauth : t.chn_source_manual}
            </div>
            <div className="mt-1 flex items-center gap-1 text-[11.5px] text-[var(--text-tertiary)]">
              {syncingNow ? <Spinner size={11} /> : <RefreshCw size={11} />}
              {syncingNow
                ? t.chn_syncing
                : c.last_synced_at && isOAuth
                  ? tt(t.chn_last_sync, { t: fmtRelative(c.last_synced_at, lang) })
                  : c.latest
                    ? tt(t.chn_last_data, { d: fmtShortDate(c.latest.date, lang) })
                    : t.chn_never_synced}
            </div>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-3">
          <Stat label={t.dsh_metric_followers} value={fmtCompact(c.latest?.followers, lang)} />
          <Stat label={t.dsh_metric_views} value={fmtCompact(c.latest?.views, lang)} />
          <Stat label={t.chn_videos} value={fmtCompact(c.latest?.video_count ?? (c.video_total || null), lang)} />
        </div>
        {c.last_error && (c.status === "error" || c.status === "needs_reauth") ? (
          <div className="mt-3 line-clamp-2 rounded-md bg-[var(--danger-soft)] px-2.5 py-1.5 text-xs text-[var(--danger)]">{c.last_error}</div>
        ) : null}
      </button>
      <div className="flex items-center gap-1 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2">
        {needsReconnect ? (
          <Button size="xs" variant="outline" leadingIcon={<Plug size={13} />} onClick={onReconnect}>
            {t.chn_reconnect}
          </Button>
        ) : isOAuth ? (
          <Button size="xs" variant="ghost" leadingIcon={<RefreshCw size={13} />} disabled={syncBusy} onClick={onSync}>
            {t.chn_sync}
          </Button>
        ) : (
          <Button size="xs" variant="ghost" leadingIcon={<LineChartIcon size={13} />} onClick={onOpen}>
            {t.chn_enter_metrics}
          </Button>
        )}
        <Button size="xs" variant="ghost" onClick={onOpen}>
          {t.chn_details}
        </Button>
        <div className="ml-auto flex items-center">
          {c.url ? (
            <IconButton size="sm" variant="ghost" label={t.chn_open_url} onClick={() => void openUrl(c.url as string)}>
              <ExternalLink size={15} />
            </IconButton>
          ) : null}
          {isOAuth && c.status !== "disconnected" ? (
            <IconButton size="sm" variant="ghost" label={t.chn_disconnect} onClick={onDisconnect}>
              <Unplug size={15} />
            </IconButton>
          ) : null}
          <IconButton size="sm" variant="ghost" label={t.chn_delete} onClick={onDelete} className="hover:!text-[var(--danger)]">
            <Trash2 size={15} />
          </IconButton>
        </div>
      </div>
    </Card>
  );
}

// --- Credentials form (shared by connect flow + settings) -------------------------

function RedirectUri({ t, uri }: { t: T; uri: string }) {
  return (
    <div className="flex min-w-0 items-center gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-muted)] py-1 pl-3 pr-1">
      <code className="min-w-0 flex-1 truncate font-[var(--font-code)] text-[12px] text-[var(--text-primary)]" title={uri}>
        {uri}
      </code>
      <CopyButton text={uri} label={t.chn_copy} copiedLabel={t.chn_copied} />
    </div>
  );
}

function CredentialForm({
  t,
  platform,
  onSaved,
  onCancel,
  submitLabel,
}: {
  t: T;
  platform: PlatformSettings;
  onSaved: (p: PlatformSettings) => void;
  onCancel?: () => void;
  submitLabel?: string;
}) {
  const [clientId, setClientId] = useState(platform.client_id);
  const [secret, setSecret] = useState("");
  const [scopes, setScopes] = useState(platform.scopes_override);
  const [showAdvanced, setShowAdvanced] = useState(!!platform.scopes_override);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const res = await updatePlatformCredentials(platform.platform, {
        client_id: clientId.trim(),
        client_secret: secret.trim() || undefined,
        scopes: showAdvanced ? scopes.trim() : undefined,
      });
      setSecret("");
      onSaved(res);
    } catch (err) {
      setError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-4">
      <Field label={t.chn_redirect_uri} help={t.chn_redirect_uri_help}>
        <RedirectUri t={t} uri={platform.redirect_uri} />
      </Field>
      <Field label={platform.platform === "tiktok" ? t.chn_client_key : t.chn_client_id} required>
        <TextField value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder={t.chn_client_id_ph} autoComplete="off" spellCheck={false} />
      </Field>
      <Field
        label={t.chn_client_secret}
        required={!platform.client_secret_set}
        help={platform.client_secret_set ? tt(t.chn_secret_keep, { m: platform.client_secret_masked }) : t.chn_secret_help}
      >
        <TextField type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" placeholder={platform.client_secret_set ? platform.client_secret_masked : ""} />
      </Field>
      <div>
        <div className="mb-1.5 text-[13px] font-semibold text-[var(--text-primary)]">{t.chn_scopes}</div>
        <div className="flex flex-wrap gap-1.5">
          {platform.scopes.map((s) => (
            <code key={s} className="rounded bg-[var(--surface-sunken)] px-1.5 py-0.5 text-[11.5px] text-[var(--text-secondary)]">
              {s}
            </code>
          ))}
        </div>
        {showAdvanced ? (
          <div className="mt-3">
            <Field label={t.chn_scopes_override} help={t.chn_scopes_override_help}>
              <TextField value={scopes} onChange={(e) => setScopes(e.target.value)} placeholder={platform.default_scopes.join(" ")} />
            </Field>
          </div>
        ) : (
          <button type="button" className="mt-2 text-xs font-medium text-[var(--text-link)] hover:underline" onClick={() => setShowAdvanced(true)}>
            {t.chn_scopes_advanced}
          </button>
        )}
      </div>
      {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" leadingIcon={<BookOpen size={14} />} onClick={() => void openUrl(platform.docs_url)}>
          {t.chn_open_docs}
        </Button>
        <div className="ml-auto flex gap-2">
          {onCancel ? (
            <Button size="sm" onClick={onCancel}>
              {t.chn_cancel}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="primary"
            type="submit"
            loading={saving}
            disabled={!clientId.trim() || (!platform.client_secret_set && !secret.trim())}
          >
            {submitLabel ?? t.chn_save}
          </Button>
        </div>
      </div>
    </form>
  );
}

// --- Connect flow --------------------------------------------------------------------

type ConnectStep = "choose" | "setup" | "starting" | "waiting" | "success" | "error";

function ConnectModal({
  t,
  open,
  initialPlatform,
  reconnect,
  settings,
  onClose,
  onSettingsChanged,
  onConnected,
  onManual,
}: {
  t: T;
  open: boolean;
  initialPlatform?: Platform;
  reconnect?: boolean;
  settings: ChannelSettings | null;
  onClose: () => void;
  onSettingsChanged: (s: ChannelSettings) => void;
  onConnected: (channels: Channel[]) => void;
  onManual: () => void;
}) {
  const [step, setStep] = useState<ConnectStep>("choose");
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [authUrl, setAuthUrl] = useState("");
  const [result, setResult] = useState<OAuthStatus | null>(null);
  const [error, setError] = useState("");
  const pollToken = useRef(0);

  const platformSettings = platform ? settings?.platforms.find((p) => p.platform === platform) ?? null : null;

  const stopPolling = () => {
    pollToken.current += 1;
  };

  const begin = useCallback(
    async (p: Platform) => {
      setPlatform(p);
      setError("");
      setStep("starting");
      const token = ++pollToken.current;
      try {
        const start = await startOAuth(p);
        if (token !== pollToken.current) return;
        setAuthUrl(start.authorize_url);
        setStep("waiting");
        void openUrl(start.authorize_url);
        const deadline = parseUtc(start.expires_at)?.getTime() ?? Date.now() + 15 * 60_000;
        while (token === pollToken.current) {
          await new Promise((r) => setTimeout(r, 1500));
          if (token !== pollToken.current) return;
          if (Date.now() > deadline) {
            setError(t.chn_oauth_expired);
            setStep("error");
            return;
          }
          let status: OAuthStatus;
          try {
            status = await getOAuthStatus(start.state);
          } catch {
            continue; // transient backend hiccup: keep waiting
          }
          if (token !== pollToken.current) return;
          if (status.status === "connected") {
            setResult(status);
            setStep("success");
            onConnected(status.channels);
            return;
          }
          if (status.status === "error" || status.status === "expired") {
            setError(status.error || (status.status === "expired" ? t.chn_oauth_expired : t.chn_oauth_failed));
            setStep("error");
            return;
          }
        }
      } catch (err) {
        if (token !== pollToken.current) return;
        setError(errText(err));
        setStep("error");
      }
    },
    [onConnected, t],
  );

  const choose = (p: Platform) => {
    const ps = settings?.platforms.find((x) => x.platform === p);
    setPlatform(p);
    if (ps && !ps.configured) setStep("setup");
    else void begin(p);
  };

  // Reset when opened; reconnect jumps straight to the chosen platform.
  useEffect(() => {
    if (!open) {
      stopPolling();
      return;
    }
    setResult(null);
    setError("");
    setAuthUrl("");
    if (initialPlatform) choose(initialPlatform);
    else {
      setPlatform(null);
      setStep("choose");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = () => {
    stopPolling();
    onClose();
  };

  const title =
    step === "choose"
      ? t.chn_connect_title
      : platform
        ? tt(reconnect ? t.chn_reconnect_title : t.chn_connect_platform, { p: PLATFORM_LABEL[platform] })
        : t.chn_connect_title;

  return (
    <Modal
      open={open}
      onClose={close}
      size="md"
      title={title}
      description={step === "choose" ? t.chn_connect_desc : step === "setup" ? t.chn_setup_desc : undefined}
      dismissible={step !== "waiting"}
    >
      {step === "choose" ? (
        <div className="grid gap-3 pb-2">
          {PLATFORMS.map((p) => {
            const ps = settings?.platforms.find((x) => x.platform === p);
            return (
              <button
                key={p}
                type="button"
                onClick={() => choose(p)}
                className="flex items-center gap-4 rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3.5 text-left transition hover:border-[color-mix(in_srgb,var(--primary)_40%,var(--border))] hover:shadow-[var(--shadow-float)]"
              >
                <PlatformIcon platform={p} size={30} />
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] font-semibold text-[var(--text-primary)]">{p === "facebook" ? t.dsh_platform_fb : PLATFORM_LABEL[p]}</div>
                  <div className="text-xs text-[var(--text-secondary)]">
                    {p === "youtube" ? t.dsh_platform_yt_desc : p === "facebook" ? t.dsh_platform_fb_desc : t.dsh_platform_tt_desc}
                  </div>
                </div>
                {ps?.configured ? (
                  <Badge tone="success" size="sm" icon={<CheckCircle2 size={11} />}>
                    {t.chn_ready}
                  </Badge>
                ) : (
                  <Badge tone="neutral" size="sm" icon={<KeyRound size={11} />}>
                    {t.chn_needs_setup}
                  </Badge>
                )}
              </button>
            );
          })}
          <div className="mt-1 flex items-center justify-between rounded-[var(--radius-panel-sm)] bg-[var(--surface-muted)] px-4 py-3 text-[13px] text-[var(--text-secondary)]">
            <span>{t.chn_manual_hint}</span>
            <Button size="sm" variant="ghost" leadingIcon={<UserPlus size={14} />} onClick={onManual}>
              {t.chn_add_manual}
            </Button>
          </div>
        </div>
      ) : null}

      {step === "setup" && platformSettings ? (
        <div className="grid gap-4 pb-2">
          <InlineNotice tone="info" title={t.chn_setup_notice_title}>
            {tt(t.chn_setup_notice, { p: PLATFORM_LABEL[platformSettings.platform] })}
          </InlineNotice>
          <CredentialForm
            t={t}
            platform={platformSettings}
            submitLabel={t.chn_save_and_connect}
            onCancel={() => setStep("choose")}
            onSaved={(ps) => {
              if (settings) onSettingsChanged({ ...settings, platforms: settings.platforms.map((x) => (x.platform === ps.platform ? ps : x)) });
              if (ps.configured) void begin(ps.platform);
            }}
          />
        </div>
      ) : null}

      {step === "starting" || step === "waiting" ? (
        <div className="flex flex-col items-center gap-4 px-4 py-8 text-center">
          <div className="relative flex h-16 w-16 items-center justify-center rounded-2xl border border-[var(--primary-soft-strong)] bg-[var(--primary-soft)]">
            {platform ? <PlatformIcon platform={platform} size={30} /> : null}
            <span className="absolute -bottom-1.5 -right-1.5 rounded-full bg-[var(--surface)] p-1 shadow-[var(--shadow-sm)]">
              <Spinner size={16} />
            </span>
          </div>
          <div>
            <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">{step === "starting" ? t.chn_oauth_starting : t.chn_oauth_waiting}</h3>
            <p className="mx-auto mt-1 max-w-sm text-[13px] leading-5 text-[var(--text-secondary)]">{t.chn_oauth_waiting_desc}</p>
          </div>
          <div className="flex gap-2">
            {authUrl ? (
              <Button size="sm" leadingIcon={<ExternalLink size={14} />} onClick={() => void openUrl(authUrl)}>
                {t.chn_oauth_reopen}
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                stopPolling();
                if (initialPlatform) close();
                else setStep("choose");
              }}
            >
              {t.chn_cancel}
            </Button>
          </div>
        </div>
      ) : null}

      {step === "success" && result ? (
        <div className="grid gap-4 py-2">
          <div className="flex flex-col items-center gap-2 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--success-soft)] text-[var(--success)]">
              <CheckCircle2 size={24} />
            </span>
            <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">{tt(t.chn_connected_n, { n: result.channels.length })}</h3>
            <p className="text-[13px] text-[var(--text-secondary)]">{t.chn_first_sync}</p>
          </div>
          <div className="grid gap-2">
            {result.channels.map((c) => (
              <div key={c.id} className="flex items-center gap-3 rounded-[var(--radius-panel-sm)] border border-[var(--border)] px-3 py-2.5">
                <Avatar src={c.avatar_url} name={c.name} size="sm" badge={<PlatformIcon platform={c.platform} size={12} />} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-semibold">{c.name}</div>
                  <div className="truncate text-xs text-[var(--text-tertiary)]">{c.handle || PLATFORM_LABEL[c.platform]}</div>
                </div>
                <ChannelStatusBadge t={t} status={c.status} />
              </div>
            ))}
          </div>
          <div className="flex justify-end">
            <Button variant="primary" onClick={close}>
              {t.chn_done}
            </Button>
          </div>
        </div>
      ) : null}

      {step === "error" ? (
        <div className="grid gap-4 py-2">
          <InlineNotice tone="danger" title={t.chn_oauth_failed}>
            {error}
          </InlineNotice>
          <div className="flex justify-end gap-2">
            {platformSettings && /not configured|client id/i.test(error) ? (
              <Button onClick={() => setStep("setup")} leadingIcon={<KeyRound size={14} />}>
                {t.chn_enter_credentials}
              </Button>
            ) : null}
            <Button onClick={close}>{t.chn_close}</Button>
            {platform ? (
              <Button variant="primary" leadingIcon={<RefreshCw size={14} />} onClick={() => void begin(platform)}>
                {t.dsh_retry}
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

// --- Manual channel ------------------------------------------------------------------

function MetricInputs({
  t,
  values,
  onChange,
  withDate = true,
}: {
  t: T;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  withDate?: boolean;
}) {
  const fields: Array<[string, string]> = [
    ["followers", t.dsh_metric_followers],
    ["views", t.dsh_metric_views],
    ["video_count", t.chn_videos],
    ["likes", t.chn_likes],
  ];
  return (
    <div
      className={
        withDate
          ? "grid grid-cols-2 gap-3 sm:grid-cols-[minmax(150px,1.25fr)_repeat(4,minmax(0,1fr))]"
          : "grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(120px,1fr))]"
      }
    >
      {withDate ? (
        <Field label={t.chn_date}>
          <TextField type="date" value={values.date ?? ""} onChange={(e) => onChange("date", e.target.value)} />
        </Field>
      ) : null}
      {fields.map(([key, label]) => (
        <Field key={key} label={label}>
          <TextField inputMode="decimal" value={values[key] ?? ""} placeholder="12.5K" onChange={(e) => onChange(key, e.target.value)} />
        </Field>
      ))}
    </div>
  );
}

/** Today's date in the user's local timezone (toISOString() would give the UTC day). */
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

function metricsPayload(values: Record<string, string>) {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(values)) if (v.trim()) out[k] = v.trim();
  return out;
}

function ManualModal({ t, open, onClose, onCreated }: { t: T; open: boolean; onClose: () => void; onCreated: (c: Channel) => void }) {
  const [platform, setPlatform] = useState<Platform>("youtube");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [handle, setHandle] = useState("");
  const [avatar, setAvatar] = useState("");
  const [metrics, setMetrics] = useState<Record<string, string>>({ date: today() });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setName("");
      setUrl("");
      setHandle("");
      setAvatar("");
      setMetrics({ date: today() });
      setError("");
    }
  }, [open]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    setSaving(true);
    setError("");
    try {
      const m = metricsPayload(metrics);
      const hasMetrics = Object.keys(m).some((k) => k !== "date");
      const created = await createManualChannel({
        platform,
        name: name.trim(),
        url: url.trim() || undefined,
        handle: handle.trim() || undefined,
        avatar_url: avatar.trim() || undefined,
        metrics: hasMetrics ? m : undefined,
      });
      onCreated(created);
    } catch (err) {
      setError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="lg"
      title={t.chn_manual_title}
      description={t.chn_manual_desc}
      footer={
        <>
          <Button onClick={onClose}>{t.chn_cancel}</Button>
          <Button variant="primary" loading={saving} disabled={!name.trim()} onClick={() => void submit()}>
            {t.chn_add_channel}
          </Button>
        </>
      }
    >
      <form onSubmit={submit} className="grid gap-5 pb-2">
        <div>
          <div className="mb-1.5 text-[13px] font-semibold">{t.chn_platform}</div>
          <Tabs
            variant="pills"
            value={platform}
            onChange={(v) => setPlatform(v as Platform)}
            items={PLATFORMS.map((p) => ({ id: p, label: PLATFORM_LABEL[p], icon: <PlatformIcon platform={p} size={13} /> }))}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t.chn_name} required>
            <TextField value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </Field>
          <Field label={t.chn_handle}>
            <TextField value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@kenh" />
          </Field>
          <Field label={t.chn_url}>
            <TextField value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
          </Field>
          <Field label={t.chn_avatar_url}>
            <TextField value={avatar} onChange={(e) => setAvatar(e.target.value)} placeholder="https://" />
          </Field>
        </div>
        <div className="rounded-[var(--radius-panel-sm)] border border-[var(--border)] bg-[var(--surface-muted)] p-4">
          <div className="mb-1 text-[13px] font-semibold">{t.chn_initial_metrics}</div>
          <p className="mb-3 text-xs text-[var(--text-secondary)]">{t.chn_metrics_hint}</p>
          <MetricInputs t={t} values={metrics} onChange={(k, v) => setMetrics((m) => ({ ...m, [k]: v }))} />
        </div>
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
        <button type="submit" hidden />
      </form>
    </Modal>
  );
}

// --- Platform settings ---------------------------------------------------------------

function SettingsDrawer({
  t,
  open,
  settings,
  onClose,
  onChanged,
}: {
  t: T;
  open: boolean;
  settings: ChannelSettings | null;
  onClose: () => void;
  onChanged: (s: ChannelSettings) => void;
}) {
  const [base, setBase] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Platform | null>(null);

  useEffect(() => {
    if (open) {
      setBase(settings?.redirect_base_url ?? "");
      setError("");
      setEditing(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const saveBase = async () => {
    setSaving(true);
    setError("");
    try {
      onChanged(await updateChannelSettings(base.trim()));
    } catch (err) {
      setError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const clearSecret = async (p: Platform) => {
    if (!settings) return;
    try {
      const ps = await updatePlatformCredentials(p, { clear_secret: true });
      onChanged({ ...settings, platforms: settings.platforms.map((x) => (x.platform === p ? ps : x)) });
    } catch (err) {
      setError(errText(err));
    }
  };

  const sourceLabel = (s: "settings" | "env" | "none") => (s === "env" ? t.chn_src_env : s === "settings" ? t.chn_src_settings : t.chn_src_none);

  return (
    <Drawer open={open} onClose={onClose} width={560} title={t.chn_platform_settings} description={t.chn_settings_desc}>
      {!settings ? (
        <Skeleton className="h-40" rounded="lg" />
      ) : (
        <div className="grid gap-5">
          <section className="grid gap-2">
            <Field label={t.chn_redirect_base} help={t.chn_redirect_base_help}>
              <div className="flex gap-2">
                <TextField value={base} onChange={(e) => setBase(e.target.value)} placeholder="https://my-vietdub.ngrok-free.app" />
                <Button onClick={saveBase} loading={saving} disabled={base.trim() === settings.redirect_base_url}>
                  {t.chn_save}
                </Button>
              </div>
            </Field>
            {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
          </section>
          {settings.platforms.map((ps) => (
            <section key={ps.platform} className="rounded-[var(--radius-panel)] border border-[var(--border)] p-4">
              <div className="flex items-center gap-3">
                <PlatformIcon platform={ps.platform} size={24} />
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] font-semibold">{ps.label}</div>
                  <div className="text-xs text-[var(--text-secondary)]">
                    {t.chn_client_id}: {ps.client_id ? `${ps.client_id.slice(0, 18)}${ps.client_id.length > 18 ? "…" : ""}` : "—"} ·{" "}
                    {sourceLabel(ps.client_id_source)}
                  </div>
                </div>
                {ps.configured ? (
                  <Badge tone="success" dot size="sm">
                    {t.chn_ready}
                  </Badge>
                ) : (
                  <Badge tone="warning" dot size="sm">
                    {t.chn_needs_setup}
                  </Badge>
                )}
              </div>
              {editing === ps.platform ? (
                <div className="mt-4">
                  <CredentialForm
                    t={t}
                    platform={ps}
                    onCancel={() => setEditing(null)}
                    onSaved={(next) => {
                      onChanged({ ...settings, platforms: settings.platforms.map((x) => (x.platform === next.platform ? next : x)) });
                      setEditing(null);
                    }}
                  />
                </div>
              ) : (
                <div className="mt-3 grid gap-3">
                  <div className="text-xs text-[var(--text-secondary)]">
                    {t.chn_client_secret}:{" "}
                    <span className="font-medium text-[var(--text-primary)]">{ps.client_secret_set ? ps.client_secret_masked : "—"}</span> ·{" "}
                    {sourceLabel(ps.client_secret_source)}
                  </div>
                  <RedirectUri t={t} uri={ps.redirect_uri} />
                  <div className="flex flex-wrap gap-2">
                    <Button size="xs" variant="outline" leadingIcon={<KeyRound size={13} />} onClick={() => setEditing(ps.platform)}>
                      {ps.configured ? t.chn_edit_credentials : t.chn_enter_credentials}
                    </Button>
                    <Button size="xs" variant="ghost" leadingIcon={<BookOpen size={13} />} onClick={() => void openUrl(ps.docs_url)}>
                      {t.chn_open_docs}
                    </Button>
                    {ps.client_secret_source === "settings" ? (
                      <Button size="xs" variant="ghost" className="ml-auto" onClick={() => void clearSecret(ps.platform)}>
                        {t.chn_clear_secret}
                      </Button>
                    ) : null}
                  </div>
                </div>
              )}
            </section>
          ))}
          <p className="text-xs leading-5 text-[var(--text-tertiary)]">{t.chn_secrets_local}</p>
        </div>
      )}
    </Drawer>
  );
}

// --- Channel detail ------------------------------------------------------------------

type DetailTab = "overview" | "videos" | "import";

function ChannelDrawer({
  t,
  lang,
  channel,
  onClose,
  onChanged,
  onSync,
  syncing,
}: {
  t: T;
  lang: string;
  channel: Channel | null;
  onClose: () => void;
  onChanged: () => void;
  onSync: () => void;
  syncing: boolean;
}) {
  const [tab, setTab] = useState<DetailTab>("overview");
  useEffect(() => {
    setTab("overview");
  }, [channel?.id]);
  if (!channel) return null;
  const c = channel;
  return (
    <Drawer
      open
      onClose={onClose}
      width={860}
      title={
        <span className="flex items-center gap-2">
          {c.name} <ChannelStatusBadge t={t} status={c.status} />
        </span>
      }
      description={`${PLATFORM_LABEL[c.platform]}${c.handle ? ` · ${c.handle}` : ""} · ${c.source === "oauth" ? t.chn_source_oauth : t.chn_source_manual}`}
    >
      <div className="grid gap-5">
        <div className="flex items-center gap-4">
          <Avatar src={c.avatar_url} name={c.name} size="xl" badge={<PlatformIcon platform={c.platform} size={18} />} />
          <div className="grid flex-1 grid-cols-4 gap-3">
            <Stat label={t.dsh_metric_followers} value={fmtCompact(c.latest?.followers, lang)} />
            <Stat label={t.dsh_metric_views} value={fmtCompact(c.latest?.views, lang)} />
            <Stat label={t.chn_videos} value={fmtCompact(c.latest?.video_count ?? (c.video_total || null), lang)} />
            <Stat label={t.chn_likes} value={fmtCompact(c.latest?.likes, lang)} />
          </div>
          <div className="flex shrink-0 gap-1.5">
            {c.url ? (
              <IconButton label={t.chn_open_url} onClick={() => void openUrl(c.url as string)}>
                <ExternalLink size={16} />
              </IconButton>
            ) : null}
            {c.source === "oauth" && c.status !== "disconnected" && c.status !== "needs_reauth" ? (
              <IconButton label={t.chn_sync} onClick={onSync} disabled={syncing}>
                <RefreshCw size={16} className={clsx(syncing && "animate-spin")} />
              </IconButton>
            ) : null}
          </div>
        </div>
        {c.last_error ? (
          <InlineNotice tone={c.status === "needs_reauth" ? "warning" : "danger"} title={t.chn_last_error}>
            {c.last_error}
          </InlineNotice>
        ) : null}
        <div className="text-xs text-[var(--text-tertiary)]">
          {c.source === "oauth" ? (
            <>
              {c.last_synced_at ? tt(t.chn_last_sync, { t: fmtDateTime(c.last_synced_at, lang) }) : t.chn_never_synced}
              {c.connected_at ? ` · ${tt(t.chn_connected_at, { t: fmtDateTime(c.connected_at, lang) })}` : ""}
            </>
          ) : c.latest ? (
            tt(t.chn_last_data, { d: fmtShortDate(c.latest.date, lang) })
          ) : (
            t.chn_no_snapshots
          )}
        </div>
        <Tabs
          value={tab}
          onChange={(v) => setTab(v as DetailTab)}
          items={[
            { id: "overview", label: t.chn_tab_overview, icon: <LineChartIcon size={14} /> },
            { id: "videos", label: t.chn_tab_videos, icon: <Film size={14} />, count: c.video_total },
            { id: "import", label: t.chn_tab_import, icon: <FileSpreadsheet size={14} /> },
          ]}
        />
        {tab === "overview" ? <OverviewTab t={t} lang={lang} channel={c} onChanged={onChanged} /> : null}
        {tab === "videos" ? <VideosTab t={t} lang={lang} channel={c} /> : null}
        {tab === "import" ? <ImportTab t={t} channel={c} onChanged={onChanged} /> : null}
      </div>
    </Drawer>
  );
}

function OverviewTab({ t, lang, channel, onChanged }: { t: T; lang: string; channel: Channel; onChanged: () => void }) {
  const [days, setDays] = useState(90);
  const [metric, setMetric] = useState<"followers" | "views" | "video_count">("followers");
  const [snaps, setSnaps] = useState<Snapshot[] | null>(null);
  const [values, setValues] = useState<Record<string, string>>({ date: today() });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    getSnapshots(channel.id, days)
      .then(setSnaps)
      .catch((err) => setError(errText(err)));
  }, [channel.id, days]);
  useEffect(load, [load, channel.updated_at]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      await addSnapshot(channel.id, metricsPayload(values));
      setValues({ date: today() });
      setSaved(true);
      load();
      onChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const labels = (snaps ?? []).map((s) => s.date);
  const color = PLATFORM_CHART_COLOR[channel.platform];
  const metricLabel = metric === "followers" ? t.dsh_metric_followers : metric === "views" ? t.dsh_metric_views : t.chn_videos;

  return (
    <div className="grid gap-5">
      <Card
        title={metricLabel}
        description={tt(t.chn_snapshots_n, { n: snaps?.length ?? 0 })}
        actions={
          <>
            <Tabs
              variant="pills"
              value={metric}
              onChange={(v) => setMetric(v as typeof metric)}
              items={[
                { id: "followers", label: t.dsh_metric_followers },
                { id: "views", label: t.dsh_metric_views },
                { id: "video_count", label: t.chn_videos },
              ]}
            />
            <Tabs
              variant="pills"
              value={String(days)}
              onChange={(v) => setDays(Number(v))}
              items={[30, 90, 365].map((d) => ({ id: String(d), label: tt(t.dsh_range_days, { n: d }) }))}
            />
          </>
        }
      >
        {snaps === null ? (
          <Skeleton className="h-52" />
        ) : (
          <LineChart
            labels={labels}
            series={[{ key: metric, label: metricLabel, color, values: (snaps ?? []).map((s) => s[metric]) }]}
            height={220}
            formatValue={(v) => fmtCompact(v, lang)}
            formatLabel={(d) => fmtShortDate(d, lang)}
            emptyLabel={t.chn_no_snapshots}
          />
        )}
      </Card>
      <Card title={t.chn_add_metrics} description={t.chn_add_metrics_desc}>
        <form onSubmit={submit} className="grid gap-3">
          <MetricInputs t={t} values={values} onChange={(k, v) => setValues((m) => ({ ...m, [k]: v }))} />
          {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
          <div className="flex items-center justify-end gap-3">
            {saved ? <span className="text-xs font-medium text-[var(--success)]">{t.chn_saved}</span> : null}
            <Button type="submit" variant="primary" size="sm" loading={saving} disabled={Object.keys(metricsPayload(values)).every((k) => k === "date")}>
              {t.chn_save_metrics}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}

const SORTABLE: Array<{ key: VideoSort; label: (t: T) => string }> = [
  { key: "published_at", label: (t) => t.chn_col_published },
  { key: "views", label: (t) => t.dsh_metric_views },
  { key: "likes", label: (t) => t.chn_likes },
  { key: "comments", label: (t) => t.chn_comments },
];

function VideosTab({ t, lang, channel }: { t: T; lang: string; channel: Channel }) {
  const [sort, setSort] = useState<VideoSort>("published_at");
  const [asc, setAsc] = useState(false);
  const [videos, setVideos] = useState<ChannelVideo[] | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState("");
  const [picking, setPicking] = useState<ChannelVideo | null>(null);

  useEffect(() => {
    setVideos(null);
    listChannelVideos(channel.id, sort, 200)
      .then(setVideos)
      .catch((err) => setError(errText(err)));
  }, [channel.id, channel.updated_at, sort]);
  useEffect(() => {
    listJobs()
      .then(setJobs)
      .catch(() => undefined);
  }, []);

  const rows = useMemo(() => {
    if (!videos) return [];
    if (!asc) return videos;
    // Server sorts desc with nulls last; ascending keeps nulls last too.
    const withVal = videos.filter((v) => v[sort] !== null);
    return [...withVal.reverse(), ...videos.filter((v) => v[sort] === null)];
  }, [videos, asc, sort]);

  const jobById = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);

  const clickSort = (key: VideoSort) => {
    if (key === sort) setAsc((a) => !a);
    else {
      setSort(key);
      setAsc(false);
    }
  };

  const link = async (video: ChannelVideo, jobId: string | null) => {
    try {
      const updated = await linkVideoToJob(video.id, jobId);
      setVideos((list) => list?.map((v) => (v.id === updated.id ? updated : v)) ?? list);
      setPicking(null);
    } catch (err) {
      setError(errText(err));
    }
  };

  return (
    <div className="grid gap-3">
      {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      <Card padding="none">
        <Table>
          <THead>
            <TR>
              <TH>{t.chn_col_video}</TH>
              {SORTABLE.map((s) => (
                <TH key={s.key} align={s.key === "published_at" ? "left" : "right"} aria-sort={sort === s.key ? (asc ? "ascending" : "descending") : undefined}>
                  <button
                    type="button"
                    onClick={() => clickSort(s.key)}
                    className={clsx(
                      "inline-flex items-center gap-1 uppercase tracking-[0.04em] hover:text-[var(--text-primary)]",
                      sort === s.key && "text-[var(--primary)]",
                    )}
                  >
                    {s.label(t)}
                    {sort === s.key ? asc ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : null}
                  </button>
                </TH>
              ))}
              <TH>{t.chn_col_dub}</TH>
            </TR>
          </THead>
          <TBody>
            {videos === null ? (
              <TableEmpty colSpan={6}>
                <Spinner />
              </TableEmpty>
            ) : rows.length === 0 ? (
              <TableEmpty colSpan={6}>{channel.source === "manual" ? t.chn_no_videos_manual : t.chn_no_videos}</TableEmpty>
            ) : (
              rows.map((v) => {
                const job = v.media_job_id ? jobById.get(v.media_job_id) : undefined;
                return (
                  <TR key={v.id}>
                    <TD className="min-w-[240px] max-w-[300px]">
                      <div className="flex min-w-0 items-center gap-3 py-1.5">
                        <div className="relative aspect-video w-20 shrink-0 overflow-hidden rounded bg-[var(--surface-sunken)]">
                          {v.thumbnail_url ? <img src={v.thumbnail_url} alt="" className="h-full w-full object-cover" /> : null}
                          {v.duration_seconds ? (
                            <span className="absolute bottom-0.5 right-0.5 rounded bg-black/70 px-1 text-[9.5px] text-white">{fmtDuration(v.duration_seconds)}</span>
                          ) : null}
                        </div>
                        <div className="min-w-0">
                          <button
                            type="button"
                            disabled={!v.url}
                            onClick={() => v.url && void openUrl(v.url)}
                            className="line-clamp-2 text-left text-[13px] font-medium leading-5 hover:text-[var(--primary)] disabled:hover:text-inherit"
                            title={v.title}
                          >
                            {v.title}
                          </button>
                          {v.kind !== "video" ? <span className="text-[11px] text-[var(--text-tertiary)]">{v.kind}</span> : null}
                        </div>
                      </div>
                    </TD>
                    <TD muted className="whitespace-nowrap">
                      {v.published_at ? fmtShortDate(v.published_at, lang) : "—"}
                    </TD>
                    <TD align="right">{fmtNumber(v.views, lang)}</TD>
                    <TD align="right" muted>
                      {fmtNumber(v.likes, lang)}
                    </TD>
                    <TD align="right" muted>
                      {fmtNumber(v.comments, lang)}
                    </TD>
                    <TD>
                      {v.media_job_id ? (
                        <div className="flex min-w-0 items-center gap-0.5">
                          <span className="flex min-w-0 flex-1">
                            <Badge tone="primary" size="sm" icon={<Clapperboard size={11} />} title={job ? jobTitle(job) : v.media_job_id}>
                              {t.chn_dubbed}
                            </Badge>
                          </span>
                          <IconButton size="sm" variant="ghost" label={t.chn_unlink} onClick={() => void link(v, null)}>
                            <Unlink size={13} />
                          </IconButton>
                        </div>
                      ) : (
                        <Button size="xs" variant="ghost" leadingIcon={<Link2 size={12} />} onClick={() => setPicking(v)}>
                          {t.chn_link_job}
                        </Button>
                      )}
                    </TD>
                  </TR>
                );
              })
            )}
          </TBody>
        </Table>
      </Card>
      <JobPicker t={t} lang={lang} video={picking} jobs={jobs} onClose={() => setPicking(null)} onPick={(job) => picking && void link(picking, job.id)} />
    </div>
  );
}

function JobPicker({
  t,
  lang,
  video,
  jobs,
  onClose,
  onPick,
}: {
  t: T;
  lang: string;
  video: ChannelVideo | null;
  jobs: Job[];
  onClose: () => void;
  onPick: (job: Job) => void;
}) {
  const [query, setQuery] = useState("");
  useEffect(() => setQuery(""), [video?.id]);
  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...jobs]
      .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""))
      .sort((a, b) => Number(b.status === "ready") - Number(a.status === "ready"))
      .filter((j) => !q || jobTitle(j).toLowerCase().includes(q) || j.video_url.toLowerCase().includes(q));
  }, [jobs, query]);
  return (
    <Modal open={!!video} onClose={onClose} size="md" title={t.chn_pick_job_title} description={video ? tt(t.chn_pick_job_desc, { v: video.title }) : ""}>
      <div className="grid grid-cols-1 gap-3 pb-2">
        <SearchInput value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t.chn_pick_job_search} autoFocus />
        {list.length === 0 ? (
          <EmptyState size="sm" icon={<Clapperboard size={18} />} title={t.chn_pick_job_empty} />
        ) : (
          <ul className="grid max-h-[420px] grid-cols-1 gap-1.5 overflow-y-auto">
            {list.map((job) => (
              <li key={job.id}>
                <button
                  type="button"
                  onClick={() => onPick(job)}
                  className="flex w-full items-center gap-3 rounded-[var(--radius-panel-sm)] border border-transparent p-2 text-left transition hover:border-[var(--border)] hover:bg-[var(--surface-hover)]"
                >
                  <JobThumb job={job} className="w-24 shrink-0 rounded" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-semibold">{jobTitle(job)}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                      <JobStatusBadge t={t} status={job.status} />
                      {fmtRelative(job.created_at, lang)}
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}

const CSV_TEMPLATES = {
  snapshots: "date,followers,views,video_count,likes,comments,shares\n2026-10-01,12000,850000,120,,,\n2026-10-08,12450,902000,123,,,\n",
  videos: "id,title,published_at,views,likes,comments,shares,duration,thumbnail_url,url\nvid-001,Video demo,2026-10-05,15000,820,45,12,4:32,,https://example.com/v/1\n",
};

function ImportTab({ t, channel, onChanged }: { t: T; channel: Channel; onChanged: () => void }) {
  const [kind, setKind] = useState<"snapshots" | "videos">("snapshots");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const downloadTemplate = () => {
    const blob = new Blob([CSV_TEMPLATES[kind]], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `vietdub-${kind}-template.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  const run = async () => {
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const res = await importChannelCsv(channel.id, file, kind);
      setResult(res);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      onChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <Tabs
        variant="pills"
        value={kind}
        onChange={(v) => {
          setKind(v as typeof kind);
          setResult(null);
        }}
        items={[
          { id: "snapshots", label: t.chn_import_snapshots },
          { id: "videos", label: t.chn_import_videos },
        ]}
      />
      <Card title={t.chn_csv_format} description={kind === "snapshots" ? t.chn_csv_snapshots_hint : t.chn_csv_videos_hint} actions={
        <Button size="xs" variant="ghost" leadingIcon={<Download size={13} />} onClick={downloadTemplate}>
          {t.chn_download_template}
        </Button>
      }>
        <pre className="overflow-x-auto rounded-md bg-[var(--surface-sunken)] p-3 font-[var(--font-code)] text-[11.5px] leading-5 text-[var(--text-secondary)]">
          {CSV_TEMPLATES[kind]}
        </pre>
      </Card>
      <label
        className={clsx(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[var(--radius-panel)] border border-dashed px-6 py-8 text-center transition",
          file ? "border-[var(--primary)] bg-[var(--primary-soft)]" : "border-[var(--border-strong)] hover:border-[var(--primary)] hover:bg-[var(--primary-soft)]",
        )}
      >
        <Upload size={20} className="text-[var(--primary)]" />
        <span className="text-[13px] font-semibold text-[var(--text-primary)]">{file ? file.name : t.chn_choose_csv}</span>
        <span className="text-xs text-[var(--text-tertiary)]">{t.chn_csv_limit}</span>
        <input ref={inputRef} type="file" accept=".csv,text/csv,.tsv,.txt" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      {result ? (
        <InlineNotice tone={result.errors.length ? "warning" : "success"} title={tt(t.chn_import_result, { i: result.imported, s: result.skipped })}>
          {result.errors.length ? (
            <ul className="mt-1 grid gap-0.5">
              {result.errors.slice(0, 8).map((e) => (
                <li key={e.row}>
                  {tt(t.chn_import_row, { r: e.row })}: {e.error}
                </li>
              ))}
            </ul>
          ) : null}
        </InlineNotice>
      ) : null}
      <div className="flex justify-end">
        <Button variant="primary" leadingIcon={<Upload size={15} />} disabled={!file} loading={busy} onClick={run}>
          {t.chn_import}
        </Button>
      </div>
    </div>
  );
}
