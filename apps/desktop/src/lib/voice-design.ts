export const INSTRUCT_GENDER = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
] as const;

export const INSTRUCT_AGE = [
  { value: "child", label: "Child" },
  { value: "teenager", label: "Teenager" },
  { value: "young adult", label: "Young adult" },
  { value: "middle-aged", label: "Middle-aged" },
  { value: "elderly", label: "Elderly" },
] as const;

export const INSTRUCT_PITCH = [
  { value: "very low pitch", label: "Very low" },
  { value: "low pitch", label: "Low" },
  { value: "moderate pitch", label: "Moderate" },
  { value: "high pitch", label: "High" },
  { value: "very high pitch", label: "Very high" },
] as const;

export const INSTRUCT_STYLE = [
  { value: "whisper", label: "Whisper" },
] as const;

export type InstructAttrs = { gender: string; age: string; pitch: string; style: string };

export function parseInstruct(instruction: string): InstructAttrs {
  const parts = instruction.toLowerCase().split(/[,，]\s*/).map((s) => s.trim()).filter(Boolean);
  const find = (opts: readonly { value: string }[]) => opts.find((o) => parts.includes(o.value))?.value ?? "";
  return {
    gender: find(INSTRUCT_GENDER),
    age: find(INSTRUCT_AGE),
    pitch: find(INSTRUCT_PITCH),
    style: find(INSTRUCT_STYLE),
  };
}

export function buildInstruct(attrs: InstructAttrs): string {
  return [attrs.gender, attrs.age, attrs.pitch, attrs.style].filter(Boolean).join(", ");
}
