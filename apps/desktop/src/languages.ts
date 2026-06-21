export type Language = { code: string; label: string };

// Compact target-language list for the tool. Codes are passed straight to the
// backend translator (source language is always auto-detected).
export const TARGET_LANGUAGES: Language[] = [
  { code: "VI", label: "Tiếng Việt" },
  { code: "EN", label: "English" },
  { code: "ZH", label: "中文 (Chinese)" },
  { code: "JA", label: "日本語 (Japanese)" },
  { code: "KO", label: "한국어 (Korean)" },
  { code: "FR", label: "Français" },
  { code: "ES", label: "Español" },
  { code: "DE", label: "Deutsch" },
  { code: "TH", label: "ไทย (Thai)" },
  { code: "ID", label: "Bahasa Indonesia" },
];
