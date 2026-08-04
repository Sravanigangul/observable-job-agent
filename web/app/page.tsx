"use client";

/**
 * The Jobvis console.
 *
 * Three wires meet here:
 *   1. the WebRTC conversation (@elevenlabs/react), which owns the microphone,
 *      the speaker and the client-tool calls;
 *   2. /api/events, the server's push channel — a finished run arrives here and
 *      gets replayed as a user message, which is the only thing that makes the
 *      agent speak unprompted;
 *   3. /api/state, the checkpoint, which fills the panels.
 */

import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import JobvisOrb from "@/components/JobvisOrb";
import { JobsPanel, PackPanel, RunPanel, TranscriptPanel } from "@/components/Panels";
import { eventsUrl, getConfig, getSessionStart, getState, type Config, type State } from "@/lib/api";
import type { OrbMode } from "@/lib/orbScene";
import { buildClientTools } from "@/lib/tools";

const EMPTY_STATE: State = {
  step: "start",
  thread_id: "",
  candidate: null,
  jobs: [],
  pack: null,
  run: { running: false },
};

type Line = { role: string; text: string };

function Console() {
  const [config, setConfig] = useState<Config | null>(null);
  const [state, setState] = useState<State>(EMPTY_STATE);
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState("");
  const [engaging, setEngaging] = useState(false);

  const addLine = useCallback((line: Line) => setLines((all) => [...all, line]), []);

  const clientTools = useMemo(
    () => buildClientTools(({ name }) => addLine({ role: "tool", text: name })),
    [addLine],
  );

  const conversation = useConversation({
    clientTools,
    onConnect: () => setError(""),
    onError: (message: unknown) => setError(String(message)),
    onMessage: ({ message, source }: { message: string; source: string }) =>
      addLine({ role: source === "user" ? "you" : "jobvis", text: message }),
  });

  // `useConversation` returns a fresh object every render; the SSE handler needs
  // the live one, so park it in a ref rather than resubscribing on every change.
  const conversationRef = useRef(conversation);
  useEffect(() => {
    conversationRef.current = conversation;
  });

  const connected = conversation.status === "connected";

  useEffect(() => {
    getConfig().then(setConfig).catch(() => setError("The Jobvis API is not reachable. Is `make jobvis` running?"));
    getState().then(setState).catch(() => undefined);
  }, []);

  // The push channel. State frames repaint the panels; run_finished makes the
  // agent talk; context tells it what happened on screen without interrupting.
  useEffect(() => {
    const source = new EventSource(eventsUrl());
    source.addEventListener("state", (event) => setState(JSON.parse((event as MessageEvent).data) as State));
    source.addEventListener("run_finished", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { text: string };
      addLine({ role: "system", text: payload.text });
      conversationRef.current.sendUserMessage(payload.text);
    });
    source.addEventListener("context", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { text: string };
      conversationRef.current.sendContextualUpdate(payload.text);
    });
    return () => source.close();
  }, [addLine]);

  const getFrequencies = useCallback(() => {
    try {
      return connected ? conversationRef.current.getOutputByteFrequencyData() : null;
    } catch {
      return null; // between connect and the first audio frame there is no analyser yet
    }
  }, [connected]);

  const mode: OrbMode = error
    ? "error"
    : engaging || conversation.status === "connecting"
      ? "connecting"
      : connected
        ? conversation.isSpeaking
          ? "speaking"
          : "listening"
        : "idle";

  const label = error
    ? error
    : mode === "connecting"
      ? "coming online…"
      : mode === "speaking"
        ? "speaking"
        : mode === "listening"
          ? "listening"
          : "standing by";

  async function toggle() {
    if (connected) {
      conversation.endSession();
      return;
    }
    setEngaging(true);
    setError("");
    try {
      const { token, dynamicVariables } = await getSessionStart();
      conversation.startSession({
        conversationToken: token,
        connectionType: "webrtc",
        dynamicVariables,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setEngaging(false);
    }
  }

  return (
    <main>
      <header>
        <h1>Jobvis</h1>
        <p className="sub">voice-directed job scout</p>
      </header>

      <JobvisOrb mode={mode} getFrequencies={getFrequencies} />
      <p className={`status ${mode}`}>{label}</p>

      {config && !config.voice_ok ? (
        <p className="muted center">{config.voice_hint}</p>
      ) : (
        <button type="button" className="button engage" onClick={toggle} disabled={engaging}>
          {connected ? "Stand down" : "Engage Jobvis"}
        </button>
      )}

      <RunPanel state={state} />
      <JobsPanel state={state} />
      <PackPanel state={state} />
      <TranscriptPanel lines={lines} />

      <footer>
        <a href={config?.wizard_url ?? "http://localhost:7860"}>Job Scout wizard ↗</a>
      </footer>
    </main>
  );
}

export default function Page() {
  return (
    <ConversationProvider>
      <Console />
    </ConversationProvider>
  );
}
