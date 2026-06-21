export type TranslationLanguageOption = {
  value: string;
  label: string;
};

export const SOURCE_LANGUAGE_OPTIONS: TranslationLanguageOption[] = [
  { value: "auto", label: "Auto detect" },
  { value: "EN", label: "English" },
  { value: "VI", label: "Vietnamese" },
  { value: "ZH-CN", label: "Chinese (Simplified)" },
  { value: "ZH-TW", label: "Chinese (Traditional)" },
  { value: "JA", label: "Japanese" },
  { value: "KO", label: "Korean" },
  { value: "ES", label: "Spanish" },
  { value: "FR", label: "French" },
  { value: "DE", label: "German" },
];

export const TARGET_LANGUAGE_OPTIONS: TranslationLanguageOption[] = [
  { value: "VI", label: "Vietnamese" },
  { value: "EN", label: "English" },
  { value: "ZH-CN", label: "Chinese (Simplified)" },
  { value: "ZH-TW", label: "Chinese (Traditional)" },
  { value: "JA", label: "Japanese" },
  { value: "KO", label: "Korean" },
  { value: "ES", label: "Spanish" },
  { value: "FR", label: "French" },
  { value: "DE", label: "German" },
];
