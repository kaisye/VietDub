import type { VoiceProfile } from "../types";

export function orderVoiceProfiles(
  voiceProfiles: VoiceProfile[],
  selectedVoice?: string | null,
  recentVoiceIds: string[] = [],
) {
  const priorityIds = [selectedVoice, ...recentVoiceIds].filter(
    (voiceId): voiceId is string => Boolean(voiceId && voiceId !== "auto"),
  );
  const byId = new Map(voiceProfiles.map((voice) => [voice.id, voice]));
  const used = new Set<string>();
  const ordered: VoiceProfile[] = [];

  for (const voiceId of priorityIds) {
    const voice = byId.get(voiceId) ?? unknownVoiceProfile(voiceId);
    if (used.has(voice.id)) continue;
    ordered.push(voice);
    used.add(voice.id);
  }

  for (const voice of voiceProfiles) {
    if (used.has(voice.id)) continue;
    ordered.push(voice);
    used.add(voice.id);
  }

  return ordered;
}

function unknownVoiceProfile(voiceId: string): VoiceProfile {
  return {
    id: voiceId,
    name: voiceId,
    locale: "Custom",
    language: "Custom",
    type: "Custom",
    description: "Voice currently assigned to this job.",
  };
}
