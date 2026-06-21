import { useEffect, useRef, useState } from "react";

import {
  clearCreateDraft,
  applyDefaultVoice,
  createDefaultDraft,
  loadCreateDraft,
  saveCreateDraft,
  type CreateDraft,
  type CreateMode,
} from "../../lib/create-draft";
import { getWorkspaceSettings } from "../../api";

export function useCreateDraft(mode: CreateMode = "quick_video") {
  const [draft, setDraft] = useState<CreateDraft>(() => createDefaultDraft(mode));
  const [hydrated, setHydrated] = useState(false);
  const defaultVoiceIdRef = useRef(createDefaultDraft(mode).voice.voice_id);
  const [defaultVoiceId, setDefaultVoiceId] = useState(createDefaultDraft(mode).voice.voice_id);

  useEffect(() => {
    setDraft(loadCreateDraft(mode));
    setHydrated(true);
    getWorkspaceSettings()
      .then((settings) => {
        const previousDefaultVoiceId = defaultVoiceIdRef.current;
        defaultVoiceIdRef.current = settings.default_voice_id;
        setDefaultVoiceId(settings.default_voice_id);
        // Always apply workspace default if the voice hasn't been explicitly
        // changed away from the system hardcoded default (even in a saved draft).
        setDraft((current) =>
          current.voice.voice_id === previousDefaultVoiceId
            ? applyDefaultVoice(current, settings.default_voice_id)
            : current,
        );
      })
      .catch(() => undefined);
  }, [mode]);

  useEffect(() => {
    if (!hydrated) return;
    const timer = window.setTimeout(() => saveCreateDraft(draft), 180);
    return () => window.clearTimeout(timer);
  }, [draft, hydrated]);

  function reset() {
    clearCreateDraft();
    setDraft(applyDefaultVoice(createDefaultDraft(mode), defaultVoiceIdRef.current));
  }

  return { draft, setDraft, hydrated, reset, defaultVoiceId };
}
