import clsx from "clsx";
import { CheckCircle2, Copy, ExternalLink, Film, Info, Link2, Sparkles, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { listJobs } from "../../api";
import { tt } from "../../i18n";
import type { T } from "../../i18n";
import type { Channel, Platform } from "../../lib/channels-api";
import { PLATFORMS, parseUtc } from "../../lib/channels-api";
import {
  POST_COLORS,
  POST_STATUSES,
  createPost,
  deletePost,
  duplicatePost,
  slotSuggestions,
  updatePost,
} from "../../lib/calendar-api";
import type { CalendarPost, PostColor, PostInput, PostStatus, SlotSuggestions } from "../../lib/calendar-api";
import { openUrl } from "../../lib/open-url";
import type { Job } from "../../types";
import { JobPicker, jobTitle } from "../assistant/JobPicker";
import { PLATFORM_LABEL, PlatformIcon, fmtCompact, fmtDateTime } from "../channels/platform";
import { Badge, Button, Drawer, Field, InlineNotice, Modal, Select, TextArea, TextField } from "../ui";
import { PublishModal } from "./PublishModal";
import { LABEL_COLOR, combineLocal, fmtDayMonth, fmtTime, statusMeta, toDateInput, toTimeInput, weekdayName } from "./shared";

export type DrawerTarget =
  | { mode: "create"; scheduledAt: Date | null; status?: PostStatus; platform?: Platform | null; channelId?: string | null }
  | { mode: "edit"; post: CalendarPost };

type Form = {
  title: string;
  platform: Platform | null;
  channelId: string;
  date: string;
  time: string;
  status: PostStatus;
  caption: string;
  hashtags: string;
  jobId: string | null;
  jobLabel: string;
  jobThumb: string | null;
  color: PostColor;
  label: string;
  notes: string;
};

function formFrom(target: DrawerTarget): Form {
  if (target.mode === "edit") {
    const p = target.post;
    const d = parseUtc(p.scheduled_at);
    return {
      title: p.title,
      platform: p.platform,
      channelId: p.channel_id ?? "",
      date: toDateInput(d),
      time: toTimeInput(d),
      status: p.status,
      caption: p.caption,
      hashtags: p.hashtags.join(" "),
      jobId: p.media_job_id,
      jobLabel: p.media_job?.title ?? (p.media_job_id ? p.media_job_id.slice(0, 8) : ""),
      jobThumb: p.media_job?.thumbnail_url ?? null,
      color: p.color,
      label: p.label,
      notes: p.notes,
    };
  }
  const d = target.scheduledAt;
  return {
    title: "",
    platform: target.platform ?? null,
    channelId: target.channelId ?? "",
    date: toDateInput(d),
    time: toTimeInput(d),
    status: target.status ?? (d ? "scheduled" : "idea"),
    caption: "",
    hashtags: "",
    jobId: null,
    jobLabel: "",
    jobThumb: null,
    color: "",
    label: "",
    notes: "",
  };
}

/** Create / edit a planned post. Real publishing is manual: the drawer only tracks it. */
export function PostDrawer({
  target,
  channels,
  t,
  lang,
  onClose,
  onSaved,
  onDeleted,
  onDuplicated,
}: {
  target: DrawerTarget | null;
  channels: Channel[];
  t: T;
  lang: string;
  onClose: () => void;
  onSaved: (post: CalendarPost, created: boolean) => void;
  onDeleted: (id: string) => void;
  onDuplicated: (post: CalendarPost) => void;
}) {
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState("");
  const [titleError, setTitleError] = useState("");
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [suggest, setSuggest] = useState<SlotSuggestions | null>(null);

  const post = target?.mode === "edit" ? target.post : null;
  const open = target !== null;

  useEffect(() => {
    if (!target) return;
    setForm(formFrom(target));
    setError("");
    setTitleError("");
    setConfirmDelete(false);
    setPublishing(false);
    setPickerOpen(false);
  }, [target]);

  const set = useCallback(<K extends keyof Form>(key: K, value: Form[K]) => {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }, []);

  const loadJobs = useCallback(() => {
    setJobsLoading(true);
    listJobs()
      .then((list) => {
        setJobs(list);
        setJobsError(null);
      })
      .catch((err) => setJobsError(err instanceof Error ? err.message : String(err)))
      .finally(() => setJobsLoading(false));
  }, []);
  useEffect(() => {
    if (pickerOpen && !jobs.length && !jobsLoading) loadJobs();
  }, [pickerOpen, jobs.length, jobsLoading, loadJobs]);

  // Suggested posting slots for the chosen platform / channel.
  const platform = form?.platform ?? null;
  const channelId = form?.channelId ?? "";
  useEffect(() => {
    if (!open || (!platform && !channelId)) {
      setSuggest(null);
      return;
    }
    let cancelled = false;
    slotSuggestions({ platform, channel_id: channelId || null, days: 7, limit: 4 })
      .then((s) => !cancelled && setSuggest(s))
      .catch(() => !cancelled && setSuggest(null));
    return () => {
      cancelled = true;
    };
  }, [open, platform, channelId]);

  const channelOptions = useMemo(
    () => channels.filter((c) => !form?.platform || c.platform === form.platform),
    [channels, form?.platform],
  );

  if (!form) return null;

  const setDate = (date: string) => {
    setForm((prev) => {
      if (!prev) return prev;
      let status = prev.status;
      if (date && status === "idea") status = "scheduled";
      if (!date && status === "scheduled") status = "idea";
      return { ...prev, date, time: date && !prev.time ? "19:00" : prev.time, status };
    });
  };

  const choosePlatform = (next: Platform | null) => {
    setForm((prev) => {
      if (!prev) return prev;
      const channel = channels.find((c) => c.id === prev.channelId);
      return { ...prev, platform: next, channelId: channel && next && channel.platform !== next ? "" : next ? prev.channelId : "" };
    });
  };

  const chooseChannel = (id: string) => {
    const channel = channels.find((c) => c.id === id);
    setForm((prev) => (prev ? { ...prev, channelId: id, platform: channel ? channel.platform : prev.platform } : prev));
  };

  const payload = (): PostInput & { title: string } => {
    const when = combineLocal(form.date, form.time);
    return {
      title: form.title.trim(),
      platform: form.platform,
      channel_id: form.channelId || null,
      scheduled_at: when ? when.toISOString() : null,
      status: form.status,
      caption: form.caption,
      hashtags: form.hashtags,
      media_job_id: form.jobId,
      color: form.color,
      label: form.label.trim(),
      notes: form.notes,
    };
  };

  const save = async () => {
    if (!form.title.trim()) {
      setTitleError(t.cal_err_title);
      return;
    }
    if (form.status === "scheduled" && !form.date) {
      setError(t.cal_err_scheduled_needs_date);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const saved = post ? await updatePost(post.id, payload()) : await createPost(payload());
      onSaved(saved, !post);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const duplicate = async () => {
    if (!post) return;
    setSaving(true);
    try {
      onDuplicated(await duplicatePost(post.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!post) return;
    setSaving(true);
    try {
      await deletePost(post.id);
      setConfirmDelete(false);
      onDeleted(post.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setConfirmDelete(false);
    } finally {
      setSaving(false);
    }
  };

  const tags = form.hashtags
    .split(/[\s,;]+/)
    .map((s) => s.replace(/^#+/, ""))
    .filter(Boolean);
  const isPublished = post?.status === "published";

  return (
    <>
      <Drawer
        open={open}
        onClose={onClose}
        width={520}
        title={post ? t.cal_edit_post : t.cal_new_post}
        description={post ? statusMeta(t, post.status).label : t.cal_drawer_desc}
        footer={
          <div className="flex w-full items-center gap-2">
            {post ? (
              <>
                <Button variant="ghost" size="sm" leadingIcon={<Trash2 size={15} />} onClick={() => setConfirmDelete(true)} disabled={saving}>
                  {t.cal_delete}
                </Button>
                <Button variant="ghost" size="sm" leadingIcon={<Copy size={15} />} onClick={() => void duplicate()} disabled={saving}>
                  {t.cal_duplicate}
                </Button>
              </>
            ) : null}
            <span className="flex-1" />
            {post && !isPublished ? (
              <Button size="sm" leadingIcon={<CheckCircle2 size={15} />} onClick={() => setPublishing(true)} disabled={saving}>
                {t.cal_mark_published}
              </Button>
            ) : null}
            <Button variant="primary" size="sm" onClick={() => void save()} loading={saving}>
              {post ? t.cal_save : t.cal_create}
            </Button>
          </div>
        }
      >
        <div className="grid gap-4">
          {isPublished && post ? (
            <div className="rounded-[var(--radius-panel-sm)] border border-[var(--success-border)] bg-[var(--success-soft)] p-3.5">
              <div className="flex items-center gap-2 text-[13px] font-semibold text-[var(--success)]">
                <CheckCircle2 size={16} />
                {tt(t.cal_published_at, { when: fmtDateTime(post.published_at, lang) })}
              </div>
              {post.channel_video ? (
                <div className="mt-2 text-[12.5px] text-[var(--text-secondary)]">
                  {post.channel_video.title} · {fmtCompact(post.channel_video.views, lang)} {t.cal_views}
                </div>
              ) : null}
              {post.published_url ? (
                <button
                  type="button"
                  onClick={() => void openUrl(post.published_url)}
                  className="mt-2 flex max-w-full items-center gap-1.5 truncate text-[12.5px] font-medium text-[var(--primary)] hover:underline"
                >
                  <ExternalLink size={13} className="shrink-0" />
                  <span className="truncate">{post.published_url}</span>
                </button>
              ) : null}
            </div>
          ) : (
            <InlineNotice tone="info">{t.cal_manual_notice}</InlineNotice>
          )}

          <Field label={t.cal_field_title} required error={titleError || undefined}>
            <TextField
              autoFocus={!post}
              value={form.title}
              maxLength={500}
              placeholder={t.cal_field_title_ph}
              onChange={(e) => {
                set("title", e.target.value);
                if (titleError) setTitleError("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void save();
              }}
            />
          </Field>

          <div className="grid gap-1.5">
            <span className="text-[13px] font-semibold text-[var(--text-primary)]">{t.cal_field_platform}</span>
            <div className="grid grid-cols-4 gap-1 rounded-[var(--radius-control)] border border-[var(--border)] bg-[var(--surface-sunken)] p-1">
              {[null, ...PLATFORMS].map((p) => {
                const active = form.platform === p;
                return (
                  <button
                    key={p ?? "none"}
                    type="button"
                    aria-pressed={active}
                    onClick={() => choosePlatform(p)}
                    className={clsx(
                      "flex min-h-8 items-center justify-center gap-1.5 rounded-md px-2 text-[12.5px] font-semibold transition",
                      active
                        ? "bg-[var(--surface)] text-[var(--text-primary)] shadow-[var(--shadow-sm)]"
                        : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
                    )}
                  >
                    {p ? <PlatformIcon platform={p} size={14} /> : null}
                    <span className="text-[12.5px] font-semibold">{p ? PLATFORM_LABEL[p] : t.cal_platform_none}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <Field label={t.cal_field_channel} help={channels.length ? undefined : t.cal_no_channels_help}>
            <Select value={form.channelId} onChange={(e) => chooseChannel(e.target.value)}>
              <option value="">{t.cal_channel_none}</option>
              {channelOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {form.platform ? c.name : `${PLATFORM_LABEL[c.platform]} · ${c.name}`}
                </option>
              ))}
            </Select>
          </Field>

          <div className="grid grid-cols-[minmax(0,1fr)_120px_minmax(0,1fr)] gap-3">
            <Field label={t.cal_field_date}>
              <TextField type="date" value={form.date} onChange={(e) => setDate(e.target.value)} />
            </Field>
            <Field label={t.cal_field_time}>
              <TextField type="time" value={form.time} disabled={!form.date} onChange={(e) => set("time", e.target.value)} />
            </Field>
            <Field label={t.cal_field_status}>
              <Select value={form.status} onChange={(e) => set("status", e.target.value as PostStatus)}>
                {POST_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {statusMeta(t, s).label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          {suggest?.slots.length && !isPublished ? (
            <div className="-mt-1.5 flex flex-wrap items-center gap-1.5">
              <span className="flex items-center gap-1 text-[12px] text-[var(--text-tertiary)]" title={suggest.basis === "history" ? t.cal_suggest_history : t.cal_suggest_default}>
                <Sparkles size={13} className="text-[var(--accent-violet)]" />
                {t.cal_suggest}
              </span>
              {suggest.slots.map((slot) => {
                const d = parseUtc(slot.scheduled_at);
                if (!d) return null;
                const active = form.date === toDateInput(d) && form.time === toTimeInput(d);
                return (
                  <button
                    key={slot.scheduled_at}
                    type="button"
                    onClick={() =>
                      setForm((prev) =>
                        prev
                          ? { ...prev, date: toDateInput(d), time: toTimeInput(d), status: prev.status === "idea" ? "scheduled" : prev.status }
                          : prev,
                      )
                    }
                    className={clsx(
                      "rounded-full border px-2 py-0.5 text-[11.5px] font-medium tabular-nums transition",
                      active
                        ? "border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary-hover)]"
                        : "border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--primary-soft-strong)] hover:text-[var(--primary)]",
                    )}
                  >
                    <span className="text-[11.5px] font-medium tabular-nums">
                      {weekdayName(t, d)} {fmtDayMonth(d, lang)} · {fmtTime(d, lang)}
                    </span>
                  </button>
                );
              })}
              <span className="text-[11.5px] text-[var(--text-tertiary)]">
                ({suggest.basis === "history" ? t.cal_suggest_history : t.cal_suggest_default})
              </span>
            </div>
          ) : null}

          <Field label={t.cal_field_caption}>
            <TextArea
              value={form.caption}
              maxLength={10000}
              rows={4}
              placeholder={t.cal_field_caption_ph}
              onChange={(e) => set("caption", e.target.value)}
            />
          </Field>

          <Field label={t.cal_field_hashtags} help={t.cal_field_hashtags_help}>
            <TextField value={form.hashtags} placeholder="#review #longtieng" onChange={(e) => set("hashtags", e.target.value)} />
          </Field>
          {tags.length ? (
            <div className="-mt-2 flex flex-wrap gap-1">
              {tags.slice(0, 30).map((tag, i) => (
                <Badge key={`${tag}-${i}`} tone="primary" size="sm">
                  #{tag}
                </Badge>
              ))}
            </div>
          ) : null}

          <div className="grid gap-1.5">
            <span className="text-[13px] font-semibold text-[var(--text-primary)]">{t.cal_field_job}</span>
            <div className="relative" data-job-picker-anchor>
              {form.jobId ? (
                <div className="flex items-center gap-2.5 rounded-[var(--radius-control)] border border-[var(--border)] p-2">
                  <span className="flex h-9 w-14 shrink-0 items-center justify-center overflow-hidden rounded-md bg-[var(--navy-900)] text-white/70">
                    {form.jobThumb ? <img src={form.jobThumb} alt="" className="h-full w-full object-cover" /> : <Film size={15} />}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--text-primary)]">{form.jobLabel}</span>
                  <Button size="xs" variant="ghost" onClick={() => setPickerOpen((v) => !v)}>
                    {t.cal_job_change}
                  </Button>
                  <button
                    type="button"
                    aria-label={t.cal_job_remove}
                    title={t.cal_job_remove}
                    onClick={() => setForm((prev) => (prev ? { ...prev, jobId: null, jobLabel: "", jobThumb: null } : prev))}
                    className="rounded-md p-1 text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)] hover:text-[var(--danger)]"
                  >
                    <X size={15} />
                  </button>
                </div>
              ) : (
                <Button block variant="outline" leadingIcon={<Link2 size={15} />} onClick={() => setPickerOpen((v) => !v)}>
                  {t.cal_job_link}
                </Button>
              )}
              <JobPicker
                open={pickerOpen}
                onClose={() => setPickerOpen(false)}
                jobs={jobs}
                loading={jobsLoading}
                error={jobsError}
                onReload={loadJobs}
                selectedId={form.jobId}
                onSelect={(job) => {
                  setForm((prev) =>
                    prev
                      ? {
                          ...prev,
                          jobId: job?.id ?? null,
                          jobLabel: job ? jobTitle(job) : "",
                          jobThumb: job?.thumbnail_url ?? null,
                          title: prev.title || (job ? jobTitle(job) : ""),
                        }
                      : prev,
                  );
                  setPickerOpen(false);
                }}
              />
            </div>
          </div>

          <div className="grid grid-cols-[auto_minmax(0,1fr)] items-end gap-3">
            <div className="grid gap-1.5">
              <span className="text-[13px] font-semibold text-[var(--text-primary)]">{t.cal_field_color}</span>
              <div className="flex h-9 items-center gap-1.5">
                <button
                  type="button"
                  aria-label={t.cal_color_none}
                  title={t.cal_color_none}
                  aria-pressed={!form.color}
                  onClick={() => set("color", "")}
                  className={clsx(
                    "flex h-6 w-6 items-center justify-center rounded-full border border-dashed border-[var(--border-strong)] text-[var(--text-tertiary)]",
                    !form.color && "ring-2 ring-[var(--primary)] ring-offset-1",
                  )}
                >
                  <X size={11} />
                </button>
                {POST_COLORS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    aria-label={c}
                    title={c}
                    aria-pressed={form.color === c}
                    onClick={() => set("color", c)}
                    style={{ background: LABEL_COLOR[c as Exclude<PostColor, "">] }}
                    className={clsx("h-6 w-6 rounded-full", form.color === c && "ring-2 ring-[var(--primary)] ring-offset-1")}
                  />
                ))}
              </div>
            </div>
            <Field label={t.cal_field_label}>
              <TextField value={form.label} maxLength={60} placeholder={t.cal_field_label_ph} onChange={(e) => set("label", e.target.value)} />
            </Field>
          </div>

          <Field label={t.cal_field_notes}>
            <TextArea value={form.notes} rows={3} maxLength={10000} placeholder={t.cal_field_notes_ph} onChange={(e) => set("notes", e.target.value)} />
          </Field>

          {post ? (
            <p className="flex items-center gap-1.5 text-[11.5px] text-[var(--text-tertiary)]">
              <Info size={12} />
              {tt(t.cal_meta, { created: fmtDateTime(post.created_at, lang), updated: fmtDateTime(post.updated_at, lang) })}
            </p>
          ) : null}

          {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
        </div>
      </Drawer>

      <Modal
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        size="sm"
        title={t.cal_delete_title}
        description={post ? tt(t.cal_delete_desc, { title: post.title }) : undefined}
        footer={
          <>
            <Button onClick={() => setConfirmDelete(false)}>{t.cal_cancel}</Button>
            <Button variant="danger" loading={saving} onClick={() => void remove()}>
              {t.cal_delete}
            </Button>
          </>
        }
      />

      {post ? (
        <PublishModal
          open={publishing}
          post={post}
          t={t}
          lang={lang}
          onClose={() => setPublishing(false)}
          onPublished={(updated) => {
            setPublishing(false);
            onSaved(updated, false);
          }}
        />
      ) : null}
    </>
  );
}
