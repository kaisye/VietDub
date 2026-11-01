import clsx from "clsx";
import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

/**
 * Table primitives. Wrap in <Card padding="none"> for the standard bordered look:
 *   <Table><THead><TR><TH>Name</TH></TR></THead><TBody><TR onClick=…><TD>…</TD></TR></TBody></Table>
 */
export function Table({ className, children, ...rest }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table {...rest} className={clsx("w-full border-collapse text-left text-[13px]", className)}>
        {children}
      </table>
    </div>
  );
}

export function THead({ className, children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead {...rest} className={clsx("bg-[var(--surface-muted)]", className)}>
      {children}
    </thead>
  );
}

export function TBody({ className, children, ...rest }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <tbody {...rest} className={clsx("[&>tr:last-child>td]:border-b-0", className)}>
      {children}
    </tbody>
  );
}

export function TR({
  className,
  children,
  selected = false,
  ...rest
}: HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return (
    <tr
      {...rest}
      aria-selected={selected || undefined}
      className={clsx(
        "transition-colors",
        rest.onClick && "cursor-pointer hover:bg-[var(--surface-hover)]",
        selected && "bg-[var(--primary-soft)]",
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function TH({
  className,
  children,
  align = "left",
  ...rest
}: ThHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" | "center" }) {
  return (
    <th
      {...rest}
      className={clsx(
        "h-9 whitespace-nowrap border-b border-[var(--border)] px-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[var(--text-tertiary)]",
        align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function TD({
  className,
  children,
  align = "left",
  muted = false,
  ...rest
}: TdHTMLAttributes<HTMLTableCellElement> & { align?: "left" | "right" | "center"; muted?: boolean }) {
  return (
    <td
      {...rest}
      className={clsx(
        "h-12 border-b border-[var(--border-subtle)] px-4 align-middle",
        muted ? "text-[var(--text-secondary)]" : "text-[var(--text-primary)]",
        align === "right" ? "text-right tabular-nums" : align === "center" ? "text-center" : "text-left",
        className,
      )}
    >
      {children}
    </td>
  );
}

/** Full-width row for "no data" inside a table body. */
export function TableEmpty({ colSpan, children }: { colSpan: number; children: ReactNode }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-10 text-center text-[13px] text-[var(--text-secondary)]">
        {children}
      </td>
    </tr>
  );
}
