import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertTriangle, FileVideo, FolderOpen, HardDrive, X } from "lucide-react";
import type { T } from "../../i18n";
import { Button } from "../ui";

const VIDEO_EXTENSIONS = ["mp4", "mov", "m4v", "mkv", "webm", "avi"];

export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** File name without extension, used as a default post title. */
export function fileStem(path: string): string {
  return fileName(path).replace(/\.[^.]+$/, "");
}

/**
 * A video file on this machine for a calendar post. Picked with the system file dialog;
 * only its path is stored (the file is not copied).
 */
export function LocalVideoField({
  t,
  path,
  exists,
  onChange,
  onError,
}: {
  t: T;
  path: string;
  exists: boolean;
  onChange: (path: string) => void;
  onError: (message: string) => void;
}) {
  const pick = async () => {
    try {
      const chosen = await open({
        multiple: false,
        directory: false,
        title: t.cal_video_pick_title,
        filters: [{ name: t.cal_video_filter, extensions: VIDEO_EXTENSIONS }],
      });
      if (typeof chosen === "string" && chosen) onChange(chosen);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  };

  const reveal = async () => {
    try {
      await invoke("reveal_local_file", { path });
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="grid gap-1.5">
      <span className="text-[13px] font-semibold text-[var(--text-primary)]">{t.cal_video_label}</span>
      {path ? (
        <div className="flex items-center gap-2.5 rounded-[var(--radius-control)] border border-[var(--border)] p-2">
          <span
            className={
              exists
                ? "flex h-9 w-14 shrink-0 items-center justify-center rounded-md bg-[var(--navy-900)] text-white/70"
                : "flex h-9 w-14 shrink-0 items-center justify-center rounded-md bg-[var(--danger-soft)] text-[var(--danger)]"
            }
          >
            {exists ? <FileVideo size={16} /> : <AlertTriangle size={16} />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium text-[var(--text-primary)]" title={path}>
              {fileName(path)}
            </span>
            <span className={exists ? "block truncate text-[11.5px] text-[var(--text-tertiary)]" : "block truncate text-[11.5px] text-[var(--danger)]"}>
              {exists ? path : t.cal_video_missing}
            </span>
          </span>
          {exists ? (
            <Button size="xs" variant="ghost" leadingIcon={<FolderOpen size={13} />} onClick={() => void reveal()}>
              {t.cal_video_reveal}
            </Button>
          ) : null}
          <Button size="xs" variant="ghost" onClick={() => void pick()}>
            {t.cal_job_change}
          </Button>
          <button
            type="button"
            aria-label={t.cal_video_remove}
            title={t.cal_video_remove}
            onClick={() => onChange("")}
            className="rounded-md p-1 text-[var(--text-tertiary)] hover:bg-[var(--surface-hover)] hover:text-[var(--danger)]"
          >
            <X size={15} />
          </button>
        </div>
      ) : (
        <Button block variant="outline" leadingIcon={<HardDrive size={15} />} onClick={() => void pick()}>
          {t.cal_video_pick}
        </Button>
      )}
    </div>
  );
}
