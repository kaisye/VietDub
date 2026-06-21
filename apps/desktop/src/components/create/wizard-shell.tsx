import { ArrowLeft, ArrowRight, RotateCcw, Save } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "../ui/button";
import { Panel } from "../ui/card";
import { InlineNotice } from "../ui/feedback";
import { StepNavigation, type StepItem } from "../ui/navigation";
import type { DraftValidation } from "../../lib/create-draft";
import { useT, tt } from "../../i18n";

export function WizardShell({
  title,
  description,
  steps,
  step,
  errors,
  children,
  onStepChange,
  onBack,
  onContinue,
  onReset,
  onSaveDraft,
  primaryLabel,
}: {
  title: string;
  description: string;
  steps: StepItem[];
  step: number;
  errors: DraftValidation;
  children: ReactNode;
  onStepChange: (step: number) => void;
  onBack: () => void;
  onContinue: () => void;
  onReset: () => void;
  onSaveDraft: () => void;
  primaryLabel?: string;
}) {
  const { t } = useT();
  const current = steps[step]?.id ?? steps[0].id;
  const navSteps = steps.map((item, index) => ({
    ...item,
    status:
      index < step
        ? ("complete" as const)
        : index === step
          ? ("current" as const)
          : ("upcoming" as const),
  }));

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-20 border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--canvas)_92%,transparent)] px-4 py-4 backdrop-blur-xl sm:px-6 lg:px-8">
        <div className="mx-auto flex max-w-[1560px] items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[11px] font-extrabold uppercase tracking-[0.12em] text-[var(--primary-hover)]">
              {tt(t.wiz_step_label, { n: step + 1, total: steps.length })}
            </p>
            <h1 className="truncate font-display text-2xl sm:text-3xl">{title}</h1>
            <p className="hidden text-sm text-[var(--text-secondary)] sm:block">{description}</p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="ghost" size="sm" leadingIcon={<RotateCcw size={15} />} onClick={onReset}>
              {t.wiz_reset}
            </Button>
            <Button variant="secondary" size="sm" leadingIcon={<Save size={15} />} onClick={onSaveDraft}>
              <span className="hidden sm:inline">{t.wiz_save_draft}</span>
              <span className="sm:hidden">{t.wiz_save_short}</span>
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid w-full max-w-[1560px] flex-1 xl:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="hidden border-r border-[var(--border-subtle)] p-4 xl:block">
          <div className="sticky top-28">
            <StepNavigation
              steps={navSteps}
              current={current}
              onChange={(id) => onStepChange(steps.findIndex((item) => item.id === id))}
            />
          </div>
        </aside>

        <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mb-5 xl:hidden">
            <StepNavigation
              steps={navSteps}
              current={current}
              onChange={(id) => onStepChange(steps.findIndex((item) => item.id === id))}
            />
          </div>
          {Object.keys(errors).length ? (
            <InlineNotice title={t.wiz_step_error} tone="danger">
              {Object.values(errors)[0]}
            </InlineNotice>
          ) : null}
          <div className={Object.keys(errors).length ? "mt-5" : ""}>{children}</div>
        </main>
      </div>

      <footer className="sticky bottom-0 z-20 border-t border-[var(--border)] bg-[color-mix(in_srgb,var(--surface)_94%,transparent)] px-4 py-3 backdrop-blur-xl sm:px-6">
        <div className="mx-auto flex max-w-[1560px] items-center justify-between gap-3">
          <Button variant="ghost" leadingIcon={<ArrowLeft size={16} />} onClick={onBack}>
            {t.wiz_back}
          </Button>
          <Button variant="primary" leadingIcon={<ArrowRight size={16} />} onClick={onContinue}>
            {primaryLabel ?? t.wiz_continue}
          </Button>
        </div>
      </footer>
    </div>
  );
}

export function EditorSection({
  title,
  description,
  children,
  actions,
  compact = false,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <Panel className={compact ? "p-4" : "p-5 sm:p-6"}>
      <div className={`${compact ? "mb-3" : "mb-6"} flex min-w-0 items-start justify-between gap-4`}>
        <div className="min-w-0">
          <h2 className={`font-display ${compact ? "text-xl" : "text-2xl sm:text-3xl"}`}>{title}</h2>
          {description ? (
            <p className={`${compact ? "mt-0.5 text-xs leading-5" : "mt-1 text-sm leading-6"} text-[var(--text-secondary)]`}>
              {description}
            </p>
          ) : null}
        </div>
        {actions}
      </div>
      {children}
    </Panel>
  );
}
