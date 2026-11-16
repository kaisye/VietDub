import clsx from "clsx";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronDown,
  Clapperboard,
  FileDown,
  History,
  Keyboard,
  Loader2,
  Redo2,
  Undo2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  deleteEditorDraft,
  listEditorDrafts,
  restoreEditorDraft,
  saveEditorDraft,
  type EditorDraft,
} from "../../api";
import { Badge, Button, IconButton, Modal, TextField } from "../../components/ui";
import { useT } from "../../i18n";
import type { EditorWorkspace } from "../../types";
import { Kbd } from "./controls";
import type { EditorController, SaveState } from "./useEditor";
import { errorText, type Translate } from "./utils";

export function Toolbar({
  editor,
  jobId,
  onBack,
  onShortcuts,
  L,
}: {
  editor: EditorController;
  jobId: string;
  onBack: () => void;
  onShortcuts: () => void;
  L: Translate;
}) {
  const { t } = useT();
  const workspace = editor.workspace as EditorWorkspace;
  const { busy, batch, historySize, saveState, dirty } = editor;
  const render = workspace.render;
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b border-[var(--border)] bg-[var(--surface)] px-3">
      <IconButton label={L("Quay lại", "Back")} size="sm" variant="ghost" onClick={onBack}>
        <ArrowLeft size={16} />
      </IconButton>
      <div className="flex min-w-0 items-center gap-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--primary-soft)] text-[var(--primary)]">
          <Clapperboard size={15} />
        </span>
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-semibold leading-tight" title={workspace.title || workspace.job_id}>
            {workspace.title || workspace.job_id}
          </div>
          <SaveStatus state={saveState} dirty={dirty} L={L} />
        </div>
      </div>
      <span className="mx-1 h-6 w-px bg-[var(--border)]" />
      <IconButton
        label={`${t.ed_undo} (⌘Z)`}
        size="sm"
        variant="ghost"
        disabled={!historySize.undo || !!busy || !!batch}
        onClick={() => void editor.travel("undo")}
      >
        <Undo2 size={16} />
      </IconButton>
      <IconButton
        label={`${t.ed_redo} (⇧⌘Z)`}
        size="sm"
        variant="ghost"
        disabled={!historySize.redo || !!busy || !!batch}
        onClick={() => void editor.travel("redo")}
      >
        <Redo2 size={16} />
      </IconButton>
      <span className="flex-1" />
      {render.status === "running" ? (
        <Badge tone="info" dot>
          {L("Đang render…", "Rendering…")}
        </Badge>
      ) : render.status === "done" ? (
        <Badge tone="success" dot>
          {L("Render xong — xem ở màn hình kết quả", "Render done — see result screen")}
        </Badge>
      ) : render.status === "failed" ? (
        <Badge tone="danger" dot>
          <span title={render.error}>{L("Render lỗi", "Render failed")}</span>
        </Badge>
      ) : null}
      <IconButton label={`${t.ed_shortcuts} (?)`} size="sm" variant="ghost" onClick={onShortcuts}>
        <Keyboard size={16} />
      </IconButton>
      <DraftsMenu jobId={jobId} beforeSave={editor.flushAll} onRestored={editor.onDraftRestored} L={L} />
      <Button
        size="sm"
        leadingIcon={<FileDown size={14} />}
        loading={busy === "export"}
        disabled={!!busy}
        onClick={() => void editor.exportFiles()}
        title={L("Xuất SRT / ASS / audio", "Export SRT / ASS / audio")}
      >
        {t.ed_export}
      </Button>
      <Button
        size="sm"
        variant="primary"
        leadingIcon={render.status === "running" ? <Loader2 size={14} className="animate-spin" /> : <Clapperboard size={14} />}
        disabled={!!busy || !!batch || render.status === "running"}
        onClick={() => void editor.startRender()}
      >
        {L("Render video", "Render video")}
      </Button>
    </header>
  );
}

function SaveStatus({ state, dirty, L }: { state: SaveState; dirty: boolean; L: Translate }) {
  const base = "flex items-center gap-1 text-[11px] leading-tight";
  if (state.failed) {
    return (
      <span className={clsx(base, "text-[var(--danger)]")} title={state.failed}>
        <AlertCircle size={11} />
        {L("Lưu lỗi — chỉnh sửa gần nhất chưa được lưu", "Save failed — latest edit not saved")}
      </span>
    );
  }
  if (state.pending > 0 || dirty) {
    return (
      <span className={clsx(base, "text-[var(--text-tertiary)]")}>
        <Loader2 size={11} className="animate-spin" />
        {L("Đang lưu…", "Saving…")}
      </span>
    );
  }
  return (
    <span className={clsx(base, "text-[var(--success)]")}>
      <Check size={11} />
      {state.savedAt ? `${L("Đã lưu", "Saved")} ${state.savedAt.toLocaleTimeString()}` : L("Tự động lưu", "Autosave on")}
    </span>
  );
}

function DraftsMenu({
  jobId,
  beforeSave,
  onRestored,
  L,
}: {
  jobId: string;
  beforeSave: () => Promise<boolean>;
  onRestored: (workspace: EditorWorkspace) => void;
  L: Translate;
}) {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<EditorDraft[]>([]);
  const [name, setName] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    listEditorDrafts(jobId).then(setDrafts).catch(() => undefined);
    const onDown = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, jobId]);

  const act = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  };

  const save = () =>
    act(async () => {
      if (!(await beforeSave())) throw new Error(L("Còn chỉnh sửa chưa lưu được.", "Some edits could not be saved."));
      const draft = await saveEditorDraft(jobId, name || new Date().toLocaleString());
      setDrafts((items) => [draft, ...items]);
      setName("");
      setMessage(L(`Đã lưu bản nháp "${draft.name}".`, `Saved draft "${draft.name}".`));
    });

  const restore = (draft: EditorDraft) =>
    act(async () => {
      await beforeSave();
      onRestored(await restoreEditorDraft(jobId, draft.id));
      setDrafts(await listEditorDrafts(jobId));
      setMessage(
        L(`Đã khôi phục "${draft.name}". Bản trước đó được tự lưu.`, `Restored "${draft.name}". The previous state was saved as a draft.`),
      );
    });

  const remove = (draft: EditorDraft) =>
    act(async () => {
      await deleteEditorDraft(jobId, draft.id);
      setDrafts((items) => items.filter((item) => item.id !== draft.id));
    });

  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="ghost" leadingIcon={<History size={14} />} trailingIcon={<ChevronDown size={13} />} onClick={() => setOpen((value) => !value)}>
        {L("Bản nháp", "Drafts")}
      </Button>
      {open ? (
        <div className="absolute right-0 top-[calc(100%+6px)] z-50 w-[340px] rounded-[var(--radius-panel)] border border-[var(--border)] bg-[var(--surface)] p-3 shadow-[var(--shadow-float)]">
          <div className="flex gap-2">
            <TextField
              placeholder={L("Tên bản nháp (tuỳ chọn)", "Draft name (optional)")}
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void save();
              }}
              className="!min-h-8 text-[12.5px]"
            />
            <Button size="sm" variant="primary" disabled={busy} onClick={() => void save()}>
              {L("Lưu", "Save")}
            </Button>
          </div>
          <p className="mt-2 text-[11.5px] leading-5 text-[var(--text-tertiary)]">
            {L("Mọi chỉnh sửa đã tự lưu. Bản nháp là bản chụp để quay lại sau.", "Edits autosave. A draft is a snapshot you can return to.")}
          </p>
          {message ? <p className="mt-1 text-[12px] text-[var(--text-secondary)]">{message}</p> : null}
          <div className="mt-2 max-h-64 overflow-y-auto">
            {drafts.length ? (
              drafts.map((draft) => (
                <div key={draft.id} className="flex items-center gap-2 border-t border-[var(--border-subtle)] py-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12.5px] font-medium">{draft.name}</div>
                    <div className="text-[11px] text-[var(--text-tertiary)]">{new Date(draft.created_at).toLocaleString()}</div>
                  </div>
                  {confirming === draft.id ? (
                    <Button size="xs" variant="primary" disabled={busy} onClick={() => void restore(draft)}>
                      {L("Chắc chắn?", "Sure?")}
                    </Button>
                  ) : (
                    <Button size="xs" disabled={busy} onClick={() => setConfirming(draft.id)}>
                      {L("Khôi phục", "Restore")}
                    </Button>
                  )}
                  <Button size="xs" variant="ghost" disabled={busy} onClick={() => void remove(draft)}>
                    {L("Xóa", "Delete")}
                  </Button>
                </div>
              ))
            ) : (
              <p className="py-2 text-[12px] text-[var(--text-tertiary)]">{L("Chưa có bản nháp nào.", "No drafts yet.")}</p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Keyboard shortcut reference (opened with ? or the keyboard icon). */
export function ShortcutsHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useT();
  const groups: { title: string; rows: [ReactNode, string][] }[] = [
    {
      title: t.ed_sc_playback,
      rows: [
        [<Kbd key="a">Space</Kbd>, t.ed_sc_play],
        [<><Kbd>J</Kbd> <Kbd>K</Kbd> <Kbd>L</Kbd></>, t.ed_sc_jkl],
        [<><Kbd>←</Kbd> <Kbd>→</Kbd></>, t.ed_sc_frame],
        [<><Kbd>⇧</Kbd> + <Kbd>←</Kbd> <Kbd>→</Kbd></>, t.ed_sc_second],
        [<><Kbd>↑</Kbd> <Kbd>↓</Kbd></>, t.ed_sc_cue],
        [<><Kbd>Home</Kbd> <Kbd>End</Kbd></>, t.ed_sc_ends],
      ],
    },
    {
      title: t.ed_sc_editing,
      rows: [
        [<><Kbd>S</Kbd> / <Kbd>⌘B</Kbd></>, t.ed_sc_split],
        [<><Kbd>Del</Kbd> / <Kbd>⌫</Kbd></>, t.ed_sc_delete],
        [<Kbd key="m">M</Kbd>, t.ed_sc_mute],
        [<Kbd key="t">T</Kbd>, t.tx_add],
        [<Kbd key="e">Enter</Kbd>, t.ed_sc_edit_text],
        [<><Kbd>⌘Z</Kbd> / <Kbd>⇧⌘Z</Kbd></>, t.ed_sc_undo],
        [<Kbd key="s">⌘S</Kbd>, t.ed_sc_save],
      ],
    },
    {
      title: t.ed_sc_timeline,
      rows: [
        [<><Kbd>+</Kbd> <Kbd>−</Kbd></>, t.ed_sc_zoom],
        [<><Kbd>⌘</Kbd> + {t.ed_sc_wheel}</>, t.ed_sc_zoom_pointer],
        [<Kbd key="f">\</Kbd>, t.ed_sc_fit],
        [<Kbd key="n">N</Kbd>, t.ed_sc_snap],
        [<><Kbd>⌘A</Kbd> / <Kbd>Esc</Kbd></>, t.ed_sc_select_all],
        [<>⇧ / ⌘ + click</>, t.ed_sc_multi],
      ],
    },
  ];
  return (
    <Modal open={open} onClose={onClose} title={t.ed_shortcuts} size="lg">
      <div className="grid gap-6 sm:grid-cols-3">
        {groups.map((group) => (
          <section key={group.title}>
            <h4 className="mb-2 text-[12px] font-semibold text-[var(--text-secondary)]">{group.title}</h4>
            <dl className="grid gap-2">
              {group.rows.map(([keys, label], index) => (
                <div key={index} className="grid gap-1">
                  <dt className="flex flex-wrap items-center gap-1 text-[11.5px] text-[var(--text-secondary)]">{keys}</dt>
                  <dd className="text-[12.5px] text-[var(--text-primary)]">{label}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Modal>
  );
}
