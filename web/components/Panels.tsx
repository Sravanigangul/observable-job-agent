"use client";

/**
 * The panels below the orb. Every value here came from the LangGraph
 * checkpoint via /api/state — the console renders facts, it never derives them.
 */

import { packUrl, type State } from "@/lib/api";

export function RunPanel({ state }: { state: State }) {
  if (state.run.running) {
    return (
      <section className="panel">
        <p className="panel-title">Working</p>
        <p className="muted">{state.run.latest_status || "working…"}</p>
      </section>
    );
  }
  if (state.jobs.length > 0) return null;
  if (state.candidate) {
    return (
      <section className="panel">
        <p className="panel-title">Ready</p>
        <p className="muted">
          Profile loaded for {state.candidate.name || "the candidate"}. Say: <b>&ldquo;Find me jobs.&rdquo;</b>
        </p>
      </section>
    );
  }
  return (
    <section className="panel">
      <p className="panel-title">No candidate</p>
      <p className="muted">
        Drop a CV in the <a href="http://localhost:7860">wizard</a> once — Jobvis remembers it from then on.
      </p>
    </section>
  );
}

export function JobsPanel({ state }: { state: State }) {
  if (state.jobs.length === 0) return null;
  return (
    <section className="panel">
      <p className="panel-title">Top matches</p>
      {state.jobs.slice(0, 3).map((job) => (
        <div className="row" key={job.job_id}>
          <span className="rank">{String(job.rank).padStart(2, "0")}</span>
          <span className="job">
            <b>{job.title}</b>
            <br />
            <span className="meta">
              {job.company} · {job.location}
            </span>
          </span>
          <span className="score">{job.fit_score}</span>
        </div>
      ))}
    </section>
  );
}

export function PackPanel({ state }: { state: State }) {
  const pack = state.pack;
  if (!pack) return null;
  return (
    <section className="panel">
      <p className="panel-title">Application ready</p>
      <b>{pack.headline}</b>
      <p className={pack.flags === 0 ? "verdict clean" : "verdict flagged"}>
        {pack.flags === 0 ? "✓" : "⚠"} {pack.verdict}
      </p>
      <div className="downloads">
        <a className="button" href={packUrl("pdf")} download>
          Download tailored CV (PDF)
        </a>
        <a className="button ghost" href={packUrl("tex")} download>
          Download .tex
        </a>
      </div>
    </section>
  );
}

export function TranscriptPanel({ lines }: { lines: { role: string; text: string }[] }) {
  return (
    <details className="panel transcript">
      <summary className="panel-title">Transcript</summary>
      {lines.length === 0 ? (
        <p className="muted">Nothing yet — engage Jobvis and say hello.</p>
      ) : (
        lines.slice(-60).map((line, index) => (
          <div key={index} className={`line ${line.role}`}>
            {line.role === "tool" ? (
              <span className="tool">⚙ {line.text}</span>
            ) : line.role === "system" ? (
              <span className="system">{line.text}</span>
            ) : (
              <>
                <b>{line.role === "you" ? "You" : "Jobvis"}:</b> {line.text}
              </>
            )}
          </div>
        ))
      )}
    </details>
  );
}
