import { CheckCircle2, CircleSlash, ExternalLink, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import { tt, useT } from "../../i18n";
import type { T } from "../../i18n";
import { openUrl } from "../../lib/open-url";
import { updateViralSettings } from "../../lib/viral-api";
import type { ProviderInfo, ViralSettings, ViralSettingsPatch } from "../../lib/viral-api";
import { Badge, Button, Drawer, Field, InlineNotice, Select, Switch, TextField } from "../ui";
import { ViralPlatformIcon } from "./shared";

const GOOGLE_CONSOLE_URL = "https://console.cloud.google.com/apis/library/youtube.googleapis.com";
const TIKHUB_URL = "https://tikhub.io";

function reliabilityLabel(t: T, r: ProviderInfo["reliability"]) {
  return { stable: t.vir_rel_stable, best_effort: t.vir_rel_best_effort, unverified: t.vir_rel_unverified }[r];
}

function reasonLabel(t: T, reason: string) {
  const map: Record<string, string> = {
    no_key: t.vir_reason_no_key,
    quota_exhausted: t.vir_reason_quota_exhausted,
    disabled: t.vir_reason_disabled,
    ytdlp_missing: t.vir_reason_ytdlp_missing,
  };
  return map[reason] ?? reason;
}

function capLabel(t: T, cap: string) {
  const map: Record<string, string> = {
    search: t.vir_cap_search,
    video: t.vir_cap_video,
    creator: t.vir_cap_creator,
    hot_words: t.vir_cap_hot_words,
    video_basic: t.vir_cap_video_basic,
  };
  return map[cap] ?? cap;
}

function providerLabel(t: T, id: ProviderInfo["id"]) {
  return {
    youtube_api: t.vir_prov_youtube_api,
    youtube_ytdlp: t.vir_prov_youtube_ytdlp,
    douyin_public: t.vir_prov_douyin_public,
    douyin_tikhub: t.vir_prov_douyin_tikhub,
  }[id];
}

export function ProviderStatusList({ providers }: { providers: ProviderInfo[] }) {
  const { t } = useT();
  return (
    <ul className="grid gap-2">
      {providers.map((p) => (
        <li key={p.id} className="flex items-start gap-3 rounded-[var(--radius-panel-sm)] border border-[var(--border-subtle)] bg-[var(--surface)] px-3 py-2.5">
          <ViralPlatformIcon platform={p.platform} size={18} className="mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[13px] font-semibold">{providerLabel(t, p.id)}</span>
              <Badge size="sm" tone={p.reliability === "stable" ? "success" : p.reliability === "best_effort" ? "warning" : "neutral"}>
                {reliabilityLabel(t, p.reliability)}
              </Badge>
            </div>
            <div className="mt-0.5 text-xs text-[var(--text-secondary)]">{p.capabilities.map((c) => capLabel(t, c)).join(" · ")}</div>
          </div>
          {p.available ? (
            <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-[var(--success)]">
              <CheckCircle2 size={14} /> {t.vir_avail_on}
            </span>
          ) : (
            <span className="flex shrink-0 items-center gap-1 text-xs text-[var(--text-tertiary)]">
              <CircleSlash size={14} /> {reasonLabel(t, p.reason)}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function SourcesDrawer({
  open,
  onClose,
  settings,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  settings: ViralSettings | null;
  onSaved: (s: ViralSettings) => void;
}) {
  const { t } = useT();
  const [form, setForm] = useState<ViralSettingsPatch>({});
  const [ytKey, setYtKey] = useState("");
  const [thKey, setThKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!open || !settings) return;
    setForm({
      youtube_mode: settings.youtube_mode,
      default_region: settings.default_region,
      douyin_mode: settings.douyin_mode,
      tikhub_base_url: settings.tikhub_base_url,
      translate_titles: settings.translate_titles,
      auto_rescan_enabled: settings.auto_rescan_enabled,
      auto_rescan_hours: settings.auto_rescan_hours,
    });
    setYtKey("");
    setThKey("");
    setError("");
    setSaved(false);
  }, [open, settings]);

  const save = async (extra: ViralSettingsPatch = {}) => {
    setSaving(true);
    setError("");
    try {
      const patch: ViralSettingsPatch = { ...form, ...extra };
      if (ytKey.trim() && extra.youtube_api_key === undefined) patch.youtube_api_key = ytKey.trim();
      if (thKey.trim() && extra.tikhub_api_key === undefined) patch.tikhub_api_key = thKey.trim();
      const next = await updateViralSettings(patch);
      onSaved(next);
      setYtKey("");
      setThKey("");
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const set = (patch: ViralSettingsPatch) => {
    setSaved(false);
    setForm((f) => ({ ...f, ...patch }));
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={500}
      title={t.vir_src_title}
      description={t.vir_src_desc}
      footer={
        <div className="flex items-center justify-end gap-2">
          {saved ? <span className="mr-auto text-xs font-medium text-[var(--success)]">{t.vir_src_saved}</span> : null}
          <Button variant="ghost" onClick={onClose}>
            {t.vir_close}
          </Button>
          <Button variant="primary" loading={saving} onClick={() => void save()}>
            {t.vir_src_save}
          </Button>
        </div>
      }
    >
      {!settings ? null : (
        <div className="grid gap-6">
          {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
          <section className="grid gap-2">
            <h3 className="text-[13px] font-semibold">{t.vir_src_status}</h3>
            <ProviderStatusList providers={settings.providers} />
            <p className="text-xs text-[var(--text-secondary)]">
              {tt(t.vir_llm_status, {
                status: settings.llm.configured ? tt(t.vir_llm_ok, { model: settings.llm.model || settings.llm.provider }) : t.vir_llm_missing,
              })}
            </p>
          </section>

          <section className="grid gap-3">
            <h3 className="flex items-center gap-2 text-[13px] font-semibold">
              <ViralPlatformIcon platform="youtube" size={16} /> YouTube
            </h3>
            <Field label={t.vir_src_yt_key} help={t.vir_src_yt_key_help}>
              <div className="flex gap-2">
                <TextField
                  type="password"
                  autoComplete="off"
                  value={ytKey}
                  onChange={(e) => setYtKey(e.target.value)}
                  placeholder={settings.youtube_api_key_set ? tt(t.vir_src_key_placeholder_set, { masked: settings.youtube_api_key_masked }) : "AIza…"}
                />
                {settings.youtube_api_key_set ? (
                  <Button variant="ghost" onClick={() => void save({ youtube_api_key: "" })}>
                    {t.vir_src_key_clear}
                  </Button>
                ) : null}
              </div>
            </Field>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button size="sm" variant="outline" leadingIcon={<KeyRound size={14} />} trailingIcon={<ExternalLink size={13} />} onClick={() => void openUrl(GOOGLE_CONSOLE_URL)}>
                {t.vir_src_get_key}
              </Button>
              <span className="text-xs text-[var(--text-secondary)]">
                {tt(t.vir_quota_used, { used: settings.youtube_quota_used_today, total: settings.youtube_quota_daily })}
              </span>
            </div>
            <Field label={t.vir_src_yt_mode}>
              <Select value={form.youtube_mode} onChange={(e) => set({ youtube_mode: e.target.value as ViralSettings["youtube_mode"] })}>
                <option value="auto">{t.vir_src_yt_auto}</option>
                <option value="api">{t.vir_src_yt_api}</option>
                <option value="ytdlp">{t.vir_src_yt_ytdlp}</option>
              </Select>
            </Field>
            <Field label={t.vir_src_region}>
              <TextField value={form.default_region ?? ""} maxLength={2} onChange={(e) => set({ default_region: e.target.value.toUpperCase() })} placeholder="US" />
            </Field>
          </section>

          <section className="grid gap-3">
            <h3 className="flex items-center gap-2 text-[13px] font-semibold">
              <ViralPlatformIcon platform="douyin" size={16} /> Douyin
            </h3>
            <InlineNotice tone="info">{t.vir_src_dy_note}</InlineNotice>
            <Field label={t.vir_src_dy_mode}>
              <Select value={form.douyin_mode} onChange={(e) => set({ douyin_mode: e.target.value as ViralSettings["douyin_mode"] })}>
                <option value="public">{t.vir_src_dy_public}</option>
                <option value="tikhub">{t.vir_src_dy_tikhub}</option>
                <option value="off">{t.vir_src_dy_off}</option>
              </Select>
            </Field>
            <Field label={t.vir_src_tikhub_key} help={t.vir_src_tikhub_help}>
              <div className="flex gap-2">
                <TextField
                  type="password"
                  autoComplete="off"
                  value={thKey}
                  onChange={(e) => setThKey(e.target.value)}
                  placeholder={settings.tikhub_api_key_set ? tt(t.vir_src_key_placeholder_set, { masked: settings.tikhub_api_key_masked }) : ""}
                />
                {settings.tikhub_api_key_set ? (
                  <Button variant="ghost" onClick={() => void save({ tikhub_api_key: "" })}>
                    {t.vir_src_key_clear}
                  </Button>
                ) : null}
              </div>
            </Field>
            <div className="grid grid-cols-[1fr_auto] items-end gap-2">
              <Field label={t.vir_src_tikhub_base}>
                <TextField value={form.tikhub_base_url ?? ""} onChange={(e) => set({ tikhub_base_url: e.target.value })} />
              </Field>
              <Button variant="ghost" trailingIcon={<ExternalLink size={13} />} onClick={() => void openUrl(TIKHUB_URL)}>
                TikHub
              </Button>
            </div>
          </section>

          <section className="grid gap-3">
            <Switch
              checked={Boolean(form.translate_titles)}
              onChange={(v) => set({ translate_titles: v })}
              label={t.vir_src_translate}
              description={t.vir_src_translate_desc}
            />
            <Switch
              checked={Boolean(form.auto_rescan_enabled)}
              onChange={(v) => set({ auto_rescan_enabled: v })}
              label={t.vir_src_auto}
              description={t.vir_src_auto_desc}
            />
            {form.auto_rescan_enabled ? (
              <Field label={t.vir_src_interval}>
                <Select value={String(form.auto_rescan_hours ?? 6)} onChange={(e) => set({ auto_rescan_hours: Number(e.target.value) })}>
                  {[1, 3, 6, 12, 24].map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </Select>
              </Field>
            ) : null}
          </section>
        </div>
      )}
    </Drawer>
  );
}
