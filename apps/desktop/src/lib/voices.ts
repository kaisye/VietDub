import type { VoiceProfile } from "../types";

export const VOICE_PROFILES: VoiceProfile[] = [
  {
    id: "none",
    name: "None",
    locale: "none",
    language: "None",
    type: "Disabled",
    description: "Keep source audio without generating a dubbed voice.",
  },
  {
    id: "vi-VN-HoaiMyNeural",
    name: "Hoai My",
    locale: "vi-VN",
    language: "Vietnamese",
    type: "Narration",
    description: "Vietnamese female narration voice for localized videos.",
  },
  {
    id: "vi-VN-NamMinhNeural",
    name: "Nam Minh",
    locale: "vi-VN",
    language: "Vietnamese",
    type: "Narration",
    description: "Vietnamese male narration voice for localized videos.",
  },
];
