import clsx from "clsx";
import { X } from "lucide-react";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

const modalSizes = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl", xl: "max-w-5xl" };

/** Centered dialog rendered in a portal. Closes on Escape and backdrop click (unless `dismissible={false}`). */
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: keyof typeof modalSizes;
  dismissible?: boolean;
}) {
  useEscape(open && dismissible, onClose);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-6">
      <div
        className="absolute inset-0 bg-[rgba(7,16,34,0.45)] backdrop-blur-[2px] [animation:vd-fade-in_150ms_ease]"
        onClick={dismissible ? onClose : undefined}
      />
      <div
        role="dialog"
        aria-modal="true"
        className={clsx(
          "relative flex max-h-[calc(100vh-48px)] w-full flex-col overflow-hidden rounded-[var(--radius-modal)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-modal)] [animation:vd-pop-in_180ms_cubic-bezier(.2,.8,.2,1)]",
          modalSizes[size],
        )}
      >
        {title || dismissible ? (
          <header className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
            <div className="min-w-0">
              {title ? <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-[var(--text-primary)]">{title}</h2> : null}
              {description ? <p className="mt-1 text-[13px] leading-5 text-[var(--text-secondary)]">{description}</p> : null}
            </div>
            {dismissible ? (
              <button
                type="button"
                aria-label="Close"
                onClick={onClose}
                className="-mr-2 -mt-1 rounded-md p-1.5 text-[var(--text-tertiary)] transition hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
              >
                <X size={18} />
              </button>
            ) : null}
          </header>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)] px-6 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

/** Side sheet (right by default) for details / inspectors / settings. */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 440,
  side = "right",
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: number;
  side?: "right" | "left";
}) {
  useEscape(open, onClose);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[100]">
      <div className="absolute inset-0 bg-[rgba(7,16,34,0.35)] [animation:vd-fade-in_150ms_ease]" onClick={onClose} />
      <aside
        role="dialog"
        aria-modal="true"
        style={{ width, animation: side === "right" ? "vd-slide-in-right 220ms cubic-bezier(.2,.8,.2,1)" : undefined }}
        className={clsx(
          "absolute top-0 flex h-full max-w-[92vw] flex-col border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-modal)]",
          side === "right" ? "right-0 border-l" : "left-0 border-r",
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-[var(--border-subtle)] px-5 py-4">
          <div className="min-w-0">
            {title ? <h2 className="text-[15px] font-semibold text-[var(--text-primary)]">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-[13px] text-[var(--text-secondary)]">{description}</p> : null}
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="-mr-1.5 rounded-md p-1.5 text-[var(--text-tertiary)] transition hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]"
          >
            <X size={18} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)] px-5 py-3">
            {footer}
          </footer>
        ) : null}
      </aside>
    </div>,
    document.body,
  );
}
