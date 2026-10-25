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
    id: "zerotts_maichi",
    name: "Mai Chi",
    locale: "vi-VN",
    language: "Vietnamese",
    type: "ZeroTTS (offline)",
    description: "ZeroTTS · Nữ trẻ · kể chuyện · nhẹ nhàng · CPU",
    engine: "zerotts",
  },
  {
    id: "zerotts_giahuy",
    name: "Gia Huy",
    locale: "vi-VN",
    language: "Vietnamese",
    type: "ZeroTTS (offline)",
    description: "ZeroTTS · Nam trẻ · kể chuyện · trầm ấm · CPU",
    engine: "zerotts",
  },
];
